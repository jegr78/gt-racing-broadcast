#!/usr/bin/env python3
"""Simulated league service for the event simulation: every call of the contract in
src/docs/design/league-service.openapi.yaml.

    GET  /racecast/v1/event?ref=        the event's races and widgets
    POST /racecast/v1/live              widget values computed from the standings
    POST /racecast/v1/result            stores the draft, a resend replaces it
    GET  /racecast/v1/result?ref=&race= the stored draft's status
    GET  /racecast/v1/graphic/<race>.png the result graphic, with --graphic

Every call needs `Authorization: Bearer <key>`; a wrong or missing key gets 401 and is
counted in `unauthorized`, never logged. A body that breaks the contract gets 400 with
the reason. Each accepted call lands in `calls(since)` with seq, time, call, ref, race,
User-Agent and the body; the key is never recorded.

    GET  /_sim/calls?since=N     calls after sequence N
    GET  /_sim/state             counters, stored results, fault rules
    POST /_sim/answer            {"call", "status", "body" | "raw", "location"}: answer a
                                 call with exactly this until an empty body clears it
    POST /_sim/fail              fault rules (loopback_http); targets `call:<call>`, `service`

Calls: event, live, result, result-status, graphic. `LeagueSim.env()` gives RACECAST_SIMULATION=1
and the RACECAST_SIM_LEAGUE_SERVICE_URL/_KEY pair.

    python3 tools/event-sim/fakes/league_sim.py --port 0 --ready-file league.json

Maintainer tool, not shipped."""
import argparse
import hmac
import json
import os
import struct
import sys
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loopback_http  # noqa: E402
import logsetup  # noqa: E402

FLAG_ENV = "RACECAST_SIMULATION"                        # simulation.FLAG
URL_ENV = "RACECAST_SIM_LEAGUE_SERVICE_URL"             # simulation.LEAGUE_SERVICE_URL
KEY_ENV = "RACECAST_SIM_LEAGUE_SERVICE_KEY"             # simulation.LEAGUE_SERVICE_KEY
BASE = "/racecast"
DEFAULT_KEY = "sim-league-key"
DEFAULT_REF = "SIM-MATCH-1"
CALLS = ("event", "live", "result", "result-status", "graphic")
RESULT_LINK = "https://league.example/sim/results/{ref}/{race}"   # never fetched


def png_bytes(width=2, height=2):
    """A small valid RGB PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\xc0\x10\x10" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def default_events():
    """One two-leg match with a score and a table widget."""
    return {DEFAULT_REF: {
        "v": 1, "title": "Sim Cup match day",
        "races": [{"race": "leg-1", "title": "Leg 1"}, {"race": "leg-2", "title": "Leg 2"}],
        "widgets": [
            {"id": "match-score", "title": "Match", "form": "score",
             "sample": {"values": [{"label": "Team A", "value": "0"},
                                   {"label": "Team B", "value": "0"}]}},
            {"id": "standings", "title": "Championship", "form": "table",
             "sample": {"value_title": "Pts", "rows": [{"rank": 1, "name": "Driver", "value": "0"}]}}]}}


def live_problem(body, events):
    """Why `body` is not a LiveState for a known event and race, or ""."""
    if not isinstance(body, dict) or body.get("v") != 1:
        return "v must be 1"
    for key in ("ref", "race", "mode", "entries"):
        if key not in body:
            return f"{key} is required"
    event = events.get(body["ref"])
    if event is None:
        return "unknown ref"
    if body["race"] not in [r["race"] for r in event["races"]]:
        return "race is not in the event"
    if body["mode"] not in ("race", "qualifying"):
        return "mode must be race or qualifying"
    if not isinstance(body["entries"], list):
        return "entries must be a list"
    for row in body["entries"]:
        if not isinstance(row, dict) or not isinstance(row.get("pos"), int) or "entry" not in row \
                or "driver" not in row:
            return "each entry needs pos, entry and driver"
    return ""


def widget_values(body):
    """A stand-in league computation: team points 1 per car beaten, and the drivers by position."""
    rows = sorted(body["entries"], key=lambda r: r["pos"])
    n = len(rows)
    teams = {}
    for r in rows:
        team = r.get("team") or r.get("entry") or "?"
        teams[team] = teams.get(team, 0) + n - r["pos"]
    score = [{"label": str(t)[:40], "value": str(p)} for t, p in list(teams.items())[:8]]
    table = [{"rank": r["pos"], "name": str(r.get("driver") or "")[:40] or "?",
              "value": str(r.get("laps") or 0)} for r in rows[:64]]
    return {"v": 1, "widgets": {"match-score": {"provisional": True, "values": score},
                                "standings": {"value_title": "Laps", "rows": table}}}


class LeagueSim(loopback_http.LoopbackServer):
    def __init__(self, port=0, key=DEFAULT_KEY, events=None, result_status="pending",
                 clock=time.time, faults=None, sleep=None, graphic=False):
        super().__init__(port, faults=faults, sleep=sleep)
        self.key = key
        self.graphic = graphic          # a result status names its graphic
        self.graphic_body = png_bytes()
        self.events = default_events() if events is None else events
        self.result_status = result_status
        self.clock = clock
        self.lock = threading.Lock()
        self._calls = []
        self.results = {}
        self.seq = 0
        self.unauthorized = 0
        self.rejected = 0
        self._answers = {}

    @property
    def url(self):
        return self.http_url + BASE

    def env(self):
        """The simulation-mode environment that points racecast at this service."""
        return {FLAG_ENV: "1", URL_ENV: self.url, KEY_ENV: self.key}

    def answer(self, call, status=200, body=None, raw=None, location=None):
        """Answer `call` with exactly this from now on; `clear_answers()` ends it."""
        if call not in CALLS:
            raise ValueError(f"call must be one of {CALLS}")
        payload = raw.encode() if isinstance(raw, str) else raw
        if payload is None:
            payload = json.dumps(body).encode()
        with self.lock:
            self._answers[call] = (int(status), payload, location)

    def clear_answers(self):
        with self.lock:
            self._answers = {}

    def calls(self, since=0, call=None):
        with self.lock:
            return [json.loads(json.dumps(c)) for c in self._calls
                    if c["seq"] > since and (call is None or c["call"] == call)]

    def snapshot(self):
        with self.lock:
            counts = {"seq": self.seq, "unauthorized": self.unauthorized, "rejected": self.rejected,
                      "results": sorted(f"{ref}/{race}" for ref, race in self.results)}
        return dict(counts, faults=self.faults(), faults_hit=self.faults_hit)

    @staticmethod
    def _call_name(method, path):
        if method == "GET" and path.startswith(BASE + "/v1/graphic/") and path.endswith(".png"):
            return "graphic"
        return {("GET", BASE + "/v1/event"): "event", ("POST", BASE + "/v1/live"): "live",
                ("POST", BASE + "/v1/result"): "result",
                ("GET", BASE + "/v1/result"): "result-status"}.get((method, path))

    def fault_keys(self, method, path, query, body):
        name = self._call_name(method, path)
        return ["service", "call:" + name] if name else []

    def _authorized(self, headers):
        got = (headers.get("Authorization") or "").encode()
        return hmac.compare_digest(got, f"Bearer {self.key}".encode())

    def route(self, method, path, query, headers, body):
        reply = loopback_http.json_reply
        if method == "GET" and path == "/_sim/calls":
            since = int(query.get("since", ["0"])[0] or 0)
            return reply({"calls": self.calls(since), "seq": self.seq})
        if method == "GET" and path == "/_sim/state":
            return reply(self.snapshot())
        if method == "POST" and path == "/_sim/answer":
            data = json.loads(body) if body.strip() else None
            if data is None:
                self.clear_answers()
            else:
                self.answer(data["call"], data.get("status", 200), data.get("body"),
                            data.get("raw"), data.get("location"))
            return reply({"ok": True})
        name = self._call_name(method, path)
        if name is None:
            return reply({"error": "not found"}, 404)
        if not self._authorized(headers):
            with self.lock:
                self.unauthorized += 1
            return reply({"error": "bad key"}, 401)
        with self.lock:
            canned = self._answers.get(name)
        if canned is not None:
            status, payload, location = canned
            return (status, "application/json", payload, {"Location": location} if location else {})
        q = {k: v[0] for k, v in query.items()}
        if name == "graphic":
            with self.lock:
                self.seq += 1
                self._calls.append({"seq": self.seq, "time": self.clock(), "call": name, "ref": None,
                                    "race": path.rsplit("/", 1)[-1][:-4],
                                    "user_agent": headers.get("User-Agent"), "body": None})
            return 200, "image/png", self.graphic_body
        doc = json.loads(body) if method == "POST" else None
        handler = getattr(self, "_on_" + name.replace("-", "_"))
        status, answer = handler(q, doc)
        if status != 200:
            with self.lock:
                self.rejected += 1
            return reply(answer, status)
        with self.lock:
            self.seq += 1
            self._calls.append({"seq": self.seq, "time": self.clock(), "call": name,
                                "ref": q.get("ref") or (doc or {}).get("ref"),
                                "race": q.get("race") or (doc or {}).get("race"),
                                "user_agent": headers.get("User-Agent"), "body": doc})
        return reply(answer)

    def _on_event(self, q, _doc):
        event = self.events.get(q.get("ref") or "")
        return (200, event) if event else (404, {"error": "unknown ref"})

    def _on_live(self, _q, doc):
        problem = live_problem(doc, self.events)
        return (400, {"error": problem}) if problem else (200, widget_values(doc))

    def _status(self, ref, race):
        out = {"v": 1, "status": self.result_status, "message": "Draft waits for a steward.",
               "link": RESULT_LINK.format(ref=ref, race=race)}
        if self.graphic:
            out["graphic_url"] = f"{self.url}/v1/graphic/{race}.png"
        return out

    def _on_result(self, _q, doc):
        if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("entries"), list):
            return 400, {"error": "v 1 with entries is required"}
        event = self.events.get(doc.get("ref"))
        if event is None or doc.get("race") not in [r["race"] for r in event["races"]]:
            return 400, {"error": "unknown ref or race"}
        with self.lock:
            self.results[(doc["ref"], doc["race"])] = doc
        return 200, self._status(doc["ref"], doc["race"])

    def _on_result_status(self, q, _doc):
        with self.lock:
            known = (q.get("ref"), q.get("race")) in self.results
        return (200, self._status(q["ref"], q["race"])) if known else (404, {"error": "no result"})


def build_parser():
    ap = argparse.ArgumentParser(description="Simulated league service answering the racecast contract.")
    ap.add_argument("--port", type=int, default=0, help="listen port, 0 picks a free one (default)")
    ap.add_argument("--key", default=DEFAULT_KEY, help="the bearer key it accepts")
    ap.add_argument("--result-status", choices=("pending", "stored"), default="pending",
                    help="the status every result gets (default pending)")
    ap.add_argument("--graphic", action="store_true", help="every result status names a PNG graphic")
    ap.add_argument("--ready-file", help="write {port, url, env} here once listening")
    return ap


def main(argv=None):
    logsetup.harden_stdio()
    args = build_parser().parse_args(argv)
    sim = LeagueSim(port=args.port, key=args.key, result_status=args.result_status,
                    graphic=args.graphic).start()
    loopback_http.serve_cli(sim, args.ready_file, {"port": sim.port, "url": sim.url, "env": sim.env()},
                            f"league-sim: {sim.url} (ref {DEFAULT_REF})")


fetch = loopback_http.fetch

if __name__ == "__main__":
    main()
