"""Client for the league's own management service (#1253).

Spec: src/docs/design/league-service.md, contract league-service.openapi.yaml. The relay
and the CLI call the service; it never calls back. Every request carries the racecast
User-Agent (http_util) and the league's bearer key, follows no redirect and reads at
most MAX_BODY bytes. Incoming bodies are typed against the contract's limits: an element
beyond a limit is dropped, a body that does not parse raises ServiceError. The key never
appears in a message, a log line or a status. Stdlib only.

`Client` is the live-state sender: its own thread, bounded calls, backoff after a
failure. The event the producer loaded lives in runtime/<profile>/league-event.json.
"""
import collections
import datetime
import http.client
import json
import logging
import os
import re
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit

import atomic_replace
import http_util
import simulation

V = 1
FILENAME = "league-event.json"
MAX_BODY = 256 * 1024
SOCKET_TIMEOUT_S = 5.0
TICK_S = 2.0
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 30.0
HOLD_S = 30.0                   # a widget value outlives its last good answer this long

MAX_REF = 128
MAX_RACES = 16
MAX_WIDGETS = 8
MAX_VALUES = 8
MAX_ROWS = 64
MAX_EVENT_TITLE = 80
MAX_TITLE = 60
MAX_LABEL = 40
MAX_VALUE = 16
MAX_MESSAGE = 200
RACE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
WIDGET_ID_RE = re.compile(r"[a-z0-9-]{1,32}")
FORMS = ("score", "table")
STATUSES = ("stored", "pending")
MODES = ("race", "qualifying")

Settings = collections.namedtuple("Settings", "url key")

LOG = logging.getLogger("racecast.league_service")


class ServiceError(Exception):
    """A failed call, worded for the producer; it never carries the key."""


def url_problem(url, environ=None):
    """Why `url` cannot be the service's base URL, or "". HTTPS only; simulation mode
    also takes its loopback http fake."""
    try:
        u = urlsplit(url)
        u.port   # noqa: B018  raises on a malformed port
    except ValueError:
        return "LEAGUE_SERVICE_URL is not a URL"
    loopback_sim = (u.scheme == "http" and simulation.active(environ)
                    and simulation.is_loopback_host(u.hostname))
    if u.scheme != "https" and not loopback_sim:
        return "LEAGUE_SERVICE_URL must be an https:// URL"
    if not u.hostname:
        return "LEAGUE_SERVICE_URL has no host"
    if "@" in u.netloc or u.query or u.fragment:
        return "LEAGUE_SERVICE_URL must not carry user info, a query or a fragment"
    return ""


def settings(url, key, environ=None):
    """(Settings or None, problem). None without a configured service; a problem when
    only one of the two values is set or the URL is refused."""
    url, key = (url or "").strip(), (key or "").strip()
    if not url and not key:
        return None, ""
    if not url:
        return None, "LEAGUE_SERVICE_KEY is set but LEAGUE_SERVICE_URL is missing"
    problem = url_problem(url, environ)
    if problem:
        return None, problem
    if not key:
        return None, "LEAGUE_SERVICE_URL is set but LEAGUE_SERVICE_KEY is missing"
    return Settings(url.rstrip("/"), key), ""


def _text(v, limit, required=True):
    """A string within `limit` characters, "" for an optional absent one, else None."""
    if v is None and not required:
        return ""
    if not isinstance(v, str) or len(v) > limit or (required and not v):
        return None
    return v


def _int(v, minimum=None):
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if minimum is None or v >= minimum else None


def parse_widget_value(doc, form=None):
    """A WidgetValue cut to its limits, or None when it is not an object. `form` keeps
    only that form's fields."""
    if not isinstance(doc, dict):
        return None
    out = {}
    if isinstance(doc.get("provisional"), bool):
        out["provisional"] = doc["provisional"]
    if form in (None, "score") and isinstance(doc.get("values"), list):
        values = []
        for item in doc["values"]:
            if not isinstance(item, dict):
                continue
            label, value = _text(item.get("label"), MAX_LABEL), _text(item.get("value"), MAX_VALUE, False)
            if label is not None and value is not None and "value" in item:
                values.append({"label": label, "value": value})
        out["values"] = values[:MAX_VALUES]
    if form in (None, "table"):
        if isinstance(doc.get("rows"), list):
            rows = []
            for item in doc["rows"]:
                if not isinstance(item, dict):
                    continue
                rank, name = _int(item.get("rank"), 1), _text(item.get("name"), MAX_LABEL)
                value = _text(item.get("value"), MAX_VALUE, False)
                if rank is not None and name is not None and value is not None and "value" in item:
                    rows.append({"rank": rank, "name": name, "value": value})
            out["rows"] = rows[:MAX_ROWS]
        title = _text(doc.get("value_title"), MAX_VALUE)
        if title is not None:
            out["value_title"] = title
    return out


def _widget(doc):
    if not isinstance(doc, dict):
        return None
    wid, title, form = doc.get("id"), _text(doc.get("title"), MAX_TITLE), doc.get("form")
    if not (isinstance(wid, str) and WIDGET_ID_RE.fullmatch(wid)) or title is None or form not in FORMS:
        return None
    out = {"id": wid, "title": title, "form": form}
    sample = parse_widget_value(doc.get("sample"), form)
    if sample is not None:
        out["sample"] = sample
    return out


def _version_ok(doc):
    return isinstance(doc, dict) and _int(doc.get("v")) == V


def parse_event(doc):
    """EventInfo as {v, title, races, widgets}, or None when it does not parse: wrong
    version, or no usable race."""
    if not _version_ok(doc) or not isinstance(doc.get("races"), list):
        return None
    races, seen = [], set()
    for item in doc["races"]:
        if not isinstance(item, dict):
            continue
        race = item.get("race")
        if not (isinstance(race, str) and RACE_RE.fullmatch(race)) or race in seen:
            continue
        title = _text(item.get("title"), MAX_TITLE, False)
        seen.add(race)
        races.append({"race": race, "title": title if title is not None else ""})
    races = races[:MAX_RACES]
    if not races:
        return None
    widgets, ids = [], set()
    for item in doc.get("widgets") if isinstance(doc.get("widgets"), list) else ():
        w = _widget(item)
        if w is not None and w["id"] not in ids:
            ids.add(w["id"])
            widgets.append(w)
    title = _text(doc.get("title"), MAX_EVENT_TITLE, False)
    return {"v": V, "title": title if title is not None else "", "races": races,
            "widgets": widgets[:MAX_WIDGETS]}


def parse_widget_values(doc, forms=None):
    """WidgetValues as {id: value}, or None when it does not parse. `forms` ({id: form})
    keeps only the widgets the event listed, each cut to its form."""
    if not _version_ok(doc) or not isinstance(doc.get("widgets"), dict):
        return None
    out = {}
    for wid, value in doc["widgets"].items():
        if len(out) >= MAX_WIDGETS:
            break
        if not WIDGET_ID_RE.fullmatch(wid) or (forms is not None and wid not in forms):
            continue
        parsed = parse_widget_value(value, (forms or {}).get(wid))
        if parsed is not None:
            out[wid] = parsed
    return out


def _https_link(v, host=None):
    if not isinstance(v, str) or len(v) > 2048:
        return None
    try:
        u = urlsplit(v)
    except ValueError:
        return None
    if u.scheme != "https" or not u.hostname or "@" in u.netloc:
        return None
    if host is not None and u.hostname != host:
        return None
    return v


def parse_result_status(doc, service_url):
    """ResultStatus as {status, message, link, graphic_url}, or None. The graphic is
    kept only on the service's own host."""
    if not _version_ok(doc) or doc.get("status") not in STATUSES:
        return None
    message = _text(doc.get("message"), MAX_MESSAGE, False)
    return {"status": doc["status"], "message": message or "",
            "link": _https_link(doc.get("link")),
            "graphic_url": _https_link(doc.get("graphic_url"), urlsplit(service_url).hostname)}


def ref_problem(ref):
    """Why `ref` cannot name an event, or ""."""
    if not isinstance(ref, str) or not ref.strip():
        return "the reference is empty"
    if len(ref) > MAX_REF:
        return f"the reference is longer than {MAX_REF} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in ref):
        return "the reference holds control characters"
    return ""


def _explain_http(code):
    if 300 <= code < 400:
        return "the league service answered with a redirect; racecast follows none"
    if code in (401, 403):
        return f"the league service refused the key (HTTP {code}): check LEAGUE_SERVICE_KEY"
    if code == 404:
        return "the league service does not know this (HTTP 404)"
    return f"the league service answered HTTP {code}"


def call(cfg, method, path, query=None, body=None, opener=None, timeout=SOCKET_TIMEOUT_S):
    """One bounded request; the decoded JSON answer. Raises ServiceError."""
    opener = opener or http_util.open_url
    url = cfg.url + path + ("?" + urlencode(query) if query else "")
    headers = {"Authorization": f"Bearer {cfg.key}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with opener(url, data=data, headers=headers, method=method, timeout=timeout,
                    allow_redirects=False) as r:
            raw = r.read(MAX_BODY + 1)
    except http_util.HTTPError as e:
        e.close()
        raise ServiceError(_explain_http(e.code)) from None
    except http_util.URLError as e:
        raise ServiceError(f"cannot reach the league service: {e.reason}") from None
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise ServiceError(f"cannot reach the league service: {type(e).__name__}") from None
    if len(raw) > MAX_BODY:
        raise ServiceError(f"the league service answered more than {MAX_BODY // 1024} KiB")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ServiceError("the league service answered something that is not JSON") from None


def fetch_event(cfg, ref, **kw):
    """GET /v1/event?ref=: the parsed EventInfo. Raises ServiceError."""
    problem = ref_problem(ref)
    if problem:
        raise ServiceError(problem)
    event = parse_event(call(cfg, "GET", "/v1/event", {"ref": ref.strip()}, **kw))
    if event is None:
        raise ServiceError("the league service's event does not match the contract (v1, at least one race)")
    return event


def post_live(cfg, body, forms=None, **kw):
    """POST /v1/live: the parsed widget values. Raises ServiceError."""
    values = parse_widget_values(call(cfg, "POST", "/v1/live", body=body, **kw), forms)
    if values is None:
        raise ServiceError("the league service's widget values do not match the contract")
    return values


def post_result(cfg, body, **kw):
    """POST /v1/result: the parsed status. Raises ServiceError."""
    status = parse_result_status(call(cfg, "POST", "/v1/result", body=body, **kw), cfg.url)
    if status is None:
        raise ServiceError("the league service's result status does not match the contract")
    return status


def fetch_result(cfg, ref, race, **kw):
    """GET /v1/result?ref=&race=: the parsed status. Raises ServiceError."""
    status = parse_result_status(call(cfg, "GET", "/v1/result", {"ref": ref, "race": race}, **kw),
                                 cfg.url)
    if status is None:
        raise ServiceError("the league service's result status does not match the contract")
    return status


LIVE_TOP = ("state", "red_flag", "lap", "remaining", "track_name")
LIVE_ROW = ("pos", "entry", "number", "driver", "team", "class", "class_pos", "laps", "gap_s",
            "interval_s", "gap_ms", "interval_ms", "lapped", "status", "pit_stops", "last_lap_ms",
            "best_ms", "finished")
LIVE_LAP = ("entry", "driver", "team", "ms")
LIVE_NULLABLE = {"state", "track_name", "stint", "lap", "remaining", "fastest", "gap_s", "interval_s", "gap_ms", "interval_ms",
                 "last_lap_ms", "best_ms"}


def _present(doc, keys):
    """The `keys` of `doc` it holds, a None only where the contract allows null."""
    return {k: doc[k] for k in keys if k in doc and (doc[k] is not None or k in LIVE_NULLABLE)}


def live_state(ref, race, snap):
    """The LiveState body: an allowlist cut from an undelayed dashboard snapshot. The
    rows are `entries` in qualifying too."""
    snap = snap or {}
    mode = snap.get("mode") if snap.get("mode") in MODES else "race"
    out = {"v": V, "ref": ref, "race": race, "mode": mode}
    out.update(_present(snap, LIVE_TOP))
    out.update({k: snap.get(k) for k in ("state", "track_name")})   # null until known
    out["red_flag"] = bool(snap.get("red_flag"))
    stint = snap.get("stint")
    out["stint"] = stint if _int(stint) is not None else None
    fl = snap.get("fastest")
    out["fastest"] = _present(fl, LIVE_LAP) if isinstance(fl, dict) else None
    rows = snap.get("qualifying") if mode == "qualifying" else snap.get("entries")
    out["entries"] = [dict(_present(r, LIVE_ROW), driver_ref="")
                      for r in rows or () if isinstance(r, dict)]
    return out


class Client:
    """The live-state sender. settings_fn() -> Settings | None; body_fn() -> LiveState
    or None while there is nothing to send; forms_fn() -> {widget id: form}."""

    def __init__(self, settings_fn, body_fn, forms_fn=dict, post=post_live,
                 clock=time.monotonic, log=LOG):
        self._settings_fn, self._body_fn, self._forms_fn = settings_fn, body_fn, forms_fn
        self._post, self._clock, self._log = post, clock, log
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._reload = True
        self._settings = None
        self._values = {}               # widget id -> (value, monotonic time of the answer)
        self._backoff = 0.0
        self._next_attempt = 0.0
        self._last_sent = None
        self._last_ok = None
        self._last_error = None
        self._logged_error = None

    def reload(self):
        """Re-read the settings on the next step and run it now."""
        self._reload = True
        self._wake.set()

    def wake(self):
        """Send now, e.g. on a position change."""
        self._wake.set()

    def values(self):
        """{widget id: value} answered within the last HOLD_S."""
        now = self._clock()
        with self._lock:
            return {k: v for k, (v, t) in self._values.items() if now - t <= HOLD_S}

    def status(self):
        with self._lock:
            now = self._clock()
            return {"configured": self._settings is not None,
                    "last_ok_age_s": None if self._last_ok is None else round(now - self._last_ok, 1),
                    "last_error": self._last_error, "backoff_s": self._backoff}

    def run(self, stop):
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception as e:      # a client bug must never take the relay down
                self._error(f"league service client: {type(e).__name__}")
                wait = TICK_S
            self._wake.wait(wait)
            self._wake.clear()

    def step(self):
        """One iteration; returns the seconds until the next one is due."""
        if self._reload:
            self._reload = False
            cfg = self._settings_fn()
            with self._lock:
                if cfg != self._settings:
                    self._values, self._backoff, self._next_attempt = {}, 0.0, 0.0
                self._settings = cfg
        if self._settings is None:
            return TICK_S
        now = self._clock()
        if now < self._next_attempt:
            return self._next_attempt - now
        body = self._body_fn()
        if body is None:
            return TICK_S
        self._last_sent = now
        try:
            values = self._post(self._settings, body, self._forms_fn())
        except ServiceError as e:
            self._fail(str(e), now)
            return self._backoff
        with self._lock:
            done = self._clock()
            for wid, value in values.items():
                self._values[wid] = (value, done)
            self._last_ok, self._backoff, self._last_error = done, 0.0, None
        if self._logged_error:
            self._log.info("league service: reachable again")
            self._logged_error = None
        return max(0.1, self._last_sent + TICK_S - self._clock())

    def _fail(self, message, now):
        self._backoff = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self._backoff * 2))
        self._next_attempt = now + self._backoff
        self._error(message)

    def _error(self, message):
        with self._lock:
            self._last_error = message
        if message != self._logged_error:
            self._logged_error = message
            self._log.warning("league service: %s", message)


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def _stored(doc):
    """A league-event.json document checked again, or None."""
    if not isinstance(doc, dict) or ref_problem(doc.get("ref")):
        return None
    event = parse_event(dict(doc, v=V))
    if event is None or doc.get("current") not in [r["race"] for r in event["races"]]:
        return None
    loaded = doc.get("loaded_at")
    return dict(event, ref=doc["ref"], current=doc["current"],
                loaded_at=loaded if isinstance(loaded, str) and len(loaded) <= 40 else "")


def read(path):
    """The loaded event {ref, title, races, widgets, current, loaded_at}, or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            return _stored(json.load(fh))
    except (OSError, ValueError, RecursionError):
        return None


def store(path, ref, event, now=None):
    """Write the loaded event. The current race stays when the reloaded event still
    lists it, else it is the first race. Returns what was written."""
    old = read(path)
    races = [r["race"] for r in event["races"]]
    current = old["current"] if old and old["ref"] == ref and old["current"] in races else races[0]
    stamp = datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc)
    doc = dict(event, ref=ref, current=current, loaded_at=stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".league-event-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
        atomic_replace.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return _stored(doc)


def clear(path):
    try:
        atomic_replace.retry_windows_file_operation(lambda: os.remove(path))
    except FileNotFoundError:
        pass  # no event loaded
