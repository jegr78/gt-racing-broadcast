"""League result export (#1254): the final classification to the league's own service.

Spec: src/docs/design/league-service.md ("Result"). When the race state turns
`finished`, the relay builds the contract's Result from the race service (classification
with roster team and number, each car's best counted lap, the confirmed fastest lap) and
the steward events of the audit log and the recording, keeps it in the race recording
and sends it on its own thread with retries. Nothing waits for the answer.

The loaded event (league-event.json) names the reference and the current race. The
current race moves on when a new race starts counting after a finished one; after the
last listed race no result is sent. Each send's answer lands in
runtime/<profile>/league-results.json, a returned graphic (same scheme, host and port as
the service, PNG only, up to 4 MiB) in runtime/<profile>/league-graphics/. Stdlib only.
"""
import datetime
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time

import atomic_replace
import http_util
import league_service

RESULTS = "league-results.json"
GRAPHICS = "league-graphics"
GRAPHIC_TARGET = "Race Results"        # the OBS source and Sheet graphic a take replaces
MAX_GRAPHIC = 4 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_DETAIL = 200
MAX_STATUSES = 64
RETRY_MIN_S = 2.0
RETRY_MAX_S = 60.0
COUNTING = ("racing", "final_lap", "finishing")
RACE_START_ACTIONS = ("reset", "new_event")
ROW_KEYS = ("pos", "entry", "number", "driver", "team", "class", "class_pos", "laps", "gap_s",
            "interval_s", "lapped", "status", "pit_stops", "last_lap_ms", "best_ms", "finished")
NULLABLE = {"gap_s", "interval_s", "last_lap_ms", "best_ms"}
LAP_KEYS = ("entry", "driver", "team", "ms")

LOG = logging.getLogger("racecast.league_result")
_files_lock = threading.Lock()


def iso(t):
    """A wall time as UTC ISO 8601 with a Z."""
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def read_audit(path, until):
    """The audit log's records with a time up to `until`, oldest first."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and _num(rec.get("ts")) and rec["ts"] <= until:
                    out.append(rec)
    except OSError:
        pass  # no audit log yet
    return out


def race_start(records, started_at=None, previous_finish=None):
    """When the race the result belongs to began: the latest of the recording's start,
    the previous finish and the last race reset or new event."""
    marks = [r["ts"] for r in records if r.get("action") in RACE_START_ACTIONS]
    return max([t for t in (started_at, previous_finish) if _num(t)] + marks, default=0.0)


def _detail(text):
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 1] + "…"


def _correction_detail(rec):
    parts = []
    if _num(rec.get("metres")) and rec["metres"]:
        parts.append(f"{rec['metres']:+.1f} m")
    if _num(rec.get("pit_stops")) and rec["pit_stops"]:
        parts.append(f"{rec['pit_stops']:+d} pit stop{'s' if abs(rec['pit_stops']) != 1 else ''}")
    text = ", ".join(parts) or "correction"
    reason = rec.get("reason")
    return _detail(f"{text}: {reason}" if isinstance(reason, str) and reason.strip() else text)


def steward_events(records, disconnects, since, until):
    """The Result's events between `since` and `until`: red flags and releases, Race
    Control's distance and pit stop corrections, confirmed disconnects. Never who acted."""
    out = []
    for rec in records:
        if not since <= rec["ts"] <= until:
            continue
        action = rec.get("action")
        if action == "red_flag":
            mode = rec.get("mode") if isinstance(rec.get("mode"), str) else ""
            out.append((rec["ts"], {"kind": "red_flag", "at": iso(rec["ts"]),
                                    "detail": _detail(f"red flag, restart {mode}".strip(", "))}))
        elif action == "release":
            out.append((rec["ts"], {"kind": "red_flag", "at": iso(rec["ts"]), "detail": "race released"}))
        elif action == "correct" and isinstance(rec.get("entry"), str):
            out.append((rec["ts"], {"kind": "correction", "at": iso(rec["ts"]), "entry": rec["entry"],
                                    "detail": _correction_detail(rec)}))
    for ev in disconnects or ():
        at = ev.get("last_packet") if _num(ev.get("last_packet")) else ev.get("opened")
        if not _num(at) or not since <= at <= until or not isinstance(ev.get("entry"), str):
            continue
        text = "confirmed disconnect"
        if _num(ev.get("dropout_s")):
            text += f", no data for {ev['dropout_s']:.0f} s"
        out.append((at, {"kind": "disconnect", "at": iso(at), "entry": ev["entry"], "detail": text}))
    return [e for _t, e in sorted(out, key=lambda x: x[0])]


def _row(r):
    row = {k: r[k] for k in ROW_KEYS if k in r and (r[k] is not None or k in NULLABLE)}
    row["driver_ref"] = ""
    return row


def body(ref, race, source, finished_at, events):
    """The contract's Result from a race service's result_source()."""
    out = {"v": league_service.V, "ref": ref, "race": race, "finished_at": iso(finished_at),
           "entries": [_row(r) for r in source.get("entries") or () if isinstance(r, dict)],
           "events": list(events)}
    if source.get("track_name"):
        out["track_name"] = source["track_name"]
    fl = source.get("fastest")
    out["fastest"] = {k: fl[k] for k in LAP_KEYS if fl.get(k) is not None} if isinstance(fl, dict) else None
    return out


def build(source, ref, race, finished_at, now, previous_finish=None):
    """(body, stored doc) of a race that finished at `finished_at`, with the steward
    events up to `now`, so a correction after the finish is in a resend."""
    records = read_audit(source.get("audit_path"), now) if source.get("audit_path") else []
    since = race_start([r for r in records if r["ts"] <= finished_at], source.get("started_at"),
                       previous_finish)
    events = steward_events(records, source.get("disconnects"), since, now)
    doc = body(ref, race, source, finished_at, events)
    return doc, {"ref": ref, "race": race, "finished_at": finished_at, "body": doc}


# --- statuses ---------------------------------------------------------------------

def results_path(runtime_dir):
    return os.path.join(runtime_dir, RESULTS)


def read_statuses(path):
    """Every recorded send, newest first."""
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh).get("items")
    except (OSError, ValueError, AttributeError):
        return []
    items = [i for i in items if isinstance(i, dict) and isinstance(i.get("race"), str)] \
        if isinstance(items, list) else []
    return sorted(items, key=lambda i: i.get("updated_at") or 0, reverse=True)


def status_of(path, ref, race):
    return next((i for i in read_statuses(path) if i.get("ref") == ref and i.get("race") == race), None)


def record_status(path, ref, race, **fields):
    """Merge `fields` into the status of (ref, race) and keep the newest MAX_STATUSES."""
    with _files_lock:
        items = read_statuses(path)
        old = next((i for i in items if i.get("ref") == ref and i.get("race") == race), {})
        new = dict(old, ref=ref, race=race, updated_at=time.time(), **fields)
        items = [new] + [i for i in items if i is not old][:MAX_STATUSES - 1]
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".league-results-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"items": items}, fh, indent=1)
            atomic_replace.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return new


# --- graphic ----------------------------------------------------------------------

def graphic_name(ref, race):
    """A file name for the graphic of (ref, race) that holds no path character."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", ref)[:40]
    return f"{slug}-{hashlib.sha256(ref.encode('utf-8')).hexdigest()[:8]}-{race}.png"


class GraphicError(Exception):
    """A refused or failed graphic fetch, worded for the producer."""


def fetch_graphic(cfg, url, dest, opener=None, deadline_s=league_service.READ_DEADLINE_S):
    """Fetch the result graphic at `url` into `dest`: only from the service's own scheme,
    host and port, no redirect, PNG only, at most MAX_GRAPHIC bytes. Raises GraphicError."""
    if league_service.same_origin(url, cfg.url) is None:
        raise GraphicError("the graphic is not on the league service's own host and port")
    opener = opener or http_util.open_url
    deadline = time.monotonic() + deadline_s
    raw = bytearray()
    try:
        with opener(url, headers={"Authorization": f"Bearer {cfg.key}", "Accept": "image/png"},
                    timeout=min(league_service.SOCKET_TIMEOUT_S, deadline_s), allow_redirects=False) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            while len(raw) <= MAX_GRAPHIC:
                if time.monotonic() > deadline:
                    raise GraphicError("the graphic took too long")
                chunk = r.read1(league_service.READ_CHUNK) if hasattr(r, "read1") else \
                    r.read(league_service.READ_CHUNK)
                if not chunk:
                    break
                raw += chunk
    except http_util.HTTPError as e:
        e.close()
        raise GraphicError(f"the graphic answered HTTP {e.code}") from None
    except (http_util.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise GraphicError(f"cannot fetch the graphic: {type(e).__name__}") from None
    if len(raw) > MAX_GRAPHIC:
        raise GraphicError(f"the graphic is larger than {MAX_GRAPHIC // (1024 * 1024)} MB")
    if ctype != "image/png" or not raw.startswith(PNG_MAGIC):
        raise GraphicError("the graphic is not a PNG")
    folder = os.path.dirname(os.path.abspath(dest))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".graphic-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        atomic_replace.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dest


def install_graphic(staged, graphics_dir):
    """Copy a staged result graphic over the Race Results graphic; its path."""
    if not os.path.isfile(staged):
        raise FileNotFoundError(staged)
    dest = os.path.join(graphics_dir, GRAPHIC_TARGET + ".png")
    os.makedirs(graphics_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=graphics_dir, prefix=".race-results-", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(staged, tmp)
        atomic_replace.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dest


# --- one send ---------------------------------------------------------------------

def send(cfg, doc, runtime_dir, post=None, fetch=None):
    """POST one Result and file the answer and its graphic. The status record; raises
    league_service.ServiceError after recording the failure."""
    ref, race = doc["ref"], doc["race"]
    path = results_path(runtime_dir)
    try:
        answer = (post or league_service.post_result)(cfg, doc)
    except league_service.ServiceError as e:
        old = status_of(path, ref, race) or {}
        record_status(path, ref, race, state="failed", error=str(e), attempts=(old.get("attempts") or 0) + 1)
        raise
    return _file_answer(cfg, ref, race, answer, runtime_dir, fetch, state="sent", sent_at=time.time(),
                        attempts=0, error="")


def refresh(cfg, ref, race, runtime_dir, get=None, fetch=None):
    """GET the status of a result sent earlier and file it. Raises ServiceError."""
    answer = (get or league_service.fetch_result)(cfg, ref, race)
    return _file_answer(cfg, ref, race, answer, runtime_dir, fetch, error="")


def _file_answer(cfg, ref, race, answer, runtime_dir, fetch, **fields):
    fields.update(status=answer["status"], message=answer["message"], link=answer["link"])
    graphic, problem = None, ""
    if answer.get("graphic_url"):
        dest = os.path.join(runtime_dir, GRAPHICS, graphic_name(ref, race))
        try:
            (fetch or fetch_graphic)(cfg, answer["graphic_url"], dest)
            graphic = os.path.basename(dest)
        except GraphicError as e:
            problem = str(e)
    old = status_of(results_path(runtime_dir), ref, race) or {}
    if graphic is None and not problem:
        graphic = old.get("graphic")
    return record_status(results_path(runtime_dir), ref, race, graphic=graphic, graphic_error=problem, **fields)


# --- relay ------------------------------------------------------------------------

class Exporter:
    """The relay's result export. `source` is the race service (race_state,
    result_source, store_result, recording_results); `settings_fn()` gives the league
    service Settings or None. observe() runs on the dashboard tick, run() on its own
    thread."""

    def __init__(self, runtime_dir, settings_fn, source, wall=time.time, clock=time.monotonic,
                 log=LOG, post=None, fetch=None, armed=True):
        self._dir = runtime_dir
        self._event_path = league_service.path_in(runtime_dir)
        self._settings_fn, self._source = settings_fn, source
        self._wall, self._clock, self._log = wall, clock, log
        self._post, self._fetch = post, fetch
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._jobs = {}                # (ref, race) -> {"doc", "next", "tries"}
        self._last = None
        self._previous_finish = None
        self._armed = armed

    def arm(self):
        """Start acting on the race state; the relay arms it once it owns its port."""
        self._armed = True

    # The tick ---------------------------------------------------------------------

    def observe(self, state):
        """Act on the race state of one tick: a finish exports, a race that starts
        counting after a finish moves the event to its next race. The first state seen
        is the baseline, so a relay restart never sends again."""
        if state is None or not self._armed:
            return
        last, self._last = self._last, state
        if state in COUNTING and last not in COUNTING:
            self._advance()
        elif state == "finished" and last not in (None, "finished"):
            self._finished()

    def _advance(self):
        ev = league_service.read(self._event_path)
        if ev is None or not ev["finished"]:
            return
        races = [r["race"] for r in ev["races"]]
        i = races.index(ev["current"])
        if i + 1 < len(races):
            league_service.update(self._event_path, current=races[i + 1], finished=False, finished_at=None)
            self._log.info("league result: the next race is %s", races[i + 1])
        else:
            league_service.update(self._event_path, finished=False, done=True)
            self._log.info("league result: the event's last race is over, no further result is sent")

    def _finished(self):
        now = self._wall()
        ev = league_service.read(self._event_path)
        target = ev is not None and not ev["done"]
        ref, race = (ev["ref"], ev["current"]) if target else (None, None)
        doc, stored = build(self._source.result_source(), ref, race, now, now, self._previous_finish)
        self._previous_finish = now
        if not self._source.store_result(stored):
            self._log.warning("league result: the race recording could not keep the result")
        if not target:
            if ev is not None:
                self._log.info("league result: past the event's last race, nothing is sent")
            return
        league_service.update(self._event_path, finished=True, finished_at=now)
        self._queue(doc)

    # Operator actions -------------------------------------------------------------

    def resend(self, race=None):
        """Send the result of `race` (default the current one) again: rebuilt from the
        live race while it still stands finished, else the copy in the recording."""
        ev = league_service.read(self._event_path)
        if ev is None:
            return {"ok": False, "error": "no league event loaded"}
        race = race or ev["current"]
        if race not in [r["race"] for r in ev["races"]]:
            return {"ok": False, "error": f"the event does not list race {race}"}
        live = race == ev["current"] and ev["finished"] and self._source.race_state() == "finished"
        if live:
            at = ev["finished_at"] if ev["finished_at"] is not None else self._wall()
            doc, stored = build(self._source.result_source(), ev["ref"], race, at, self._wall(),
                                self._previous_finish_before(at))
            self._source.store_result(stored)
        else:
            kept = [r for r in self._source.recording_results() if (r.get("ref"), r.get("race")) == (ev["ref"], race)]
            if not kept:
                return {"ok": False, "error": f"race {race} has no result in the race recording yet"}
            doc = kept[-1]["body"]
        self._queue(doc)
        return {"ok": True, "ref": ev["ref"], "race": race, "rebuilt": live}

    def _previous_finish_before(self, at):
        prev = self._previous_finish
        return prev if prev is not None and prev < at else None

    def set_race(self, race):
        """The Director sets the current race; it counts as not finished yet."""
        try:
            ev = league_service.update(self._event_path, current=race, finished=False, done=False,
                                       finished_at=None)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if ev is None:
            return {"ok": False, "error": "no league event loaded"}
        return {"ok": True, "current": ev["current"]}

    def status(self):
        """The panel's view: the event, where it stands, the result statuses and the
        sends still waiting. Never the key."""
        cfg, problem = self._settings_fn()
        ev = league_service.read(self._event_path)
        out = {"configured": cfg is not None, "problem": problem, "event": None, "results": [],
               "queued": [], "note": "", "watching": self._armed and self._last is not None}
        if ev is None:
            return out
        out["event"] = {k: ev[k] for k in ("ref", "title", "races", "current", "finished", "done")}
        out["results"] = [i for i in read_statuses(results_path(self._dir)) if i.get("ref") == ev["ref"]]
        with self._lock:
            out["queued"] = [{"race": race, "tries": job["tries"]} for (ref, race), job in self._jobs.items()
                             if ref == ev["ref"]]
        if ev["done"]:
            out["note"] = (f"The event's {len(ev['races'])} listed races are over: "
                           "a further race sends no result.")
        return out

    def staged_graphic(self, race):
        """The fetched graphic of `race` in the loaded event, or None."""
        ev = league_service.read(self._event_path)
        if ev is None or not isinstance(race, str) or race not in [r["race"] for r in ev["races"]]:
            return None
        path = os.path.join(self._dir, GRAPHICS, graphic_name(ev["ref"], race))
        return path if os.path.isfile(path) else None

    # The sender -------------------------------------------------------------------

    def _queue(self, doc):
        key = (doc["ref"], doc["race"])
        with self._lock:
            self._jobs[key] = {"doc": doc, "next": 0.0, "tries": 0}
        record_status(results_path(self._dir), doc["ref"], doc["race"], state="sending", error="")
        self._wake.set()

    def run(self, stop):
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception as e:      # noqa: BLE001  the sender never takes the relay down
                self._log.warning("league result sender: %s", type(e).__name__)
                wait = RETRY_MIN_S
            self._wake.wait(wait)
            self._wake.clear()

    def step(self):
        """Send the jobs that are due; the seconds until the next one."""
        now = self._clock()
        with self._lock:
            due = [(k, j) for k, j in self._jobs.items() if j["next"] <= now]
        for key, job in due:
            self._attempt(key, job)
        with self._lock:
            waits = [j["next"] - self._clock() for j in self._jobs.values()]
        return max(0.1, min(waits)) if waits else RETRY_MAX_S

    def _attempt(self, key, job):
        cfg, problem = self._settings_fn()
        try:
            if cfg is None:
                raise league_service.ServiceError(problem or "no league service configured")
            send(cfg, job["doc"], self._dir, post=self._post, fetch=self._fetch)
        except league_service.ServiceError as e:
            if cfg is None:
                record_status(results_path(self._dir), key[0], key[1], state="failed", error=str(e))
            with self._lock:
                if self._jobs.get(key) is job:
                    job["tries"] += 1
                    job["next"] = self._clock() + min(RETRY_MAX_S, RETRY_MIN_S * 2 ** (job["tries"] - 1))
            self._log.warning("league result %s: %s (retrying)", key[1], e)
            return
        with self._lock:
            if self._jobs.get(key) is job:
                del self._jobs[key]
        self._log.info("league result %s sent", key[1])
