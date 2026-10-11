"""Read-only public projection and one bounded shared A/B preparation worker."""

import collections
import copy
import hashlib
import json
import queue
import re
import threading

import gt7_comparison
from race_engineer_access import AccessError

ROUTES = frozenset(("snapshot", "history", "lap", "reference", "compare"))
FIELDS = frozenset((
    "id",
    "session_id",
    "version",
    "context_revision",
    "lap",
    "state",
    "detail_state",
    "gt7_time_s",
    "time_s",
    "status",
    "reason",
    "car_id",
    "car",
    "track_id",
    "track",
    "layout",
    "distance_m",
    "length_m",
    "fuel_used_l",
    "top_speed_kmh",
    "lap_role",
    "after_service",
    "capture_complete",
    "trace_complete",
    "time_valid",
    "data_quality",
    "reasons",
    "pace_eligible",
    "comparison_eligible",
    "analysis_role",
    "compound",
    "compounds",
    "context_confirmed",
    "context_warnings",
    "tyre_age_laps",
    "settings_confirmed",
    "stint_id",
    "provisional_comparison_eligible",
    "elapsed_s",
    "observed_from_line",
    "definition_fingerprint",
    "sector_variant_id",
    "track_definition_warnings",
))
TRACE = frozenset((
    "d",
    "t",
    "speed_kmh",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "steer_deg",
    "x",
    "z",
    "y",
    "fuel",
))
SETTINGS = frozenset((
    "bop",
    "fixed_setup",
    "tyre_x",
    "fuel_x",
    "time_progression",
    "time_of_day",
))


def project(value, fields):
    return {k: copy.deepcopy(v) for k, v in value.items() if k in fields}


def notes(rows):
    return [
        dict(
            project(n, ("id", "scope", "text")),
            **(
                {
                    "position": project(
                        n["position"], ("x", "y", "z", "d", "t", "reference_version")
                    )
                }
                if n.get("position")
                else {}
            ),
        )
        for n in rows
    ]


def public_lap(row, detailed=True):
    result = project(row, FIELDS)
    result["context_settings"] = project(row.get("context_settings", {}), SETTINGS)
    result["strategy"] = project(row.get("strategy", {}), ("fuel_map", "shortshift"))
    if row.get("strategy", {}).get("targets"):
        result["strategy"]["targets"] = project(
            row["strategy"]["targets"], tuple(str(i) for i in range(1, 16))
        )
    if detailed:
        result.update(
            trace=[project(p, TRACE) for p in row.get("trace", [])],
            sectors=copy.deepcopy(row.get("sectors", [])),
            notes=notes(row.get("notes", [])),
        )
        if row.get("larger_sectors"):
            result["larger_sectors"] = project(
                row["larger_sectors"],
                (
                    "variant_id",
                    "name",
                    "kind",
                    "reviewed",
                    "bounds_m",
                    "names",
                    "times_s",
                ),
            )
        result["corner_metrics"] = [
            project(
                c,
                (
                    "id",
                    "number",
                    "name",
                    "direction",
                    "entry_m",
                    "apex_m",
                    "exit_m",
                    "max_brake_pct",
                    "anchor_speed_kmh",
                    "min_speed_kmh",
                    "entry_speed_kmh",
                    "exit_speed_kmh",
                    "brake_start_m",
                    "throttle_start_m",
                    "time_s",
                    "warnings",
                ),
            )
            for c in row.get("corner_metrics", [])
        ]
        result["combination_metrics"] = [
            project(c, ("id", "name", "numbers", "start_m", "end_m", "time_s"))
            for c in row.get("combination_metrics", [])
        ]
    return result


def selector(value):
    if not isinstance(value, str) or len(value) > 120:
        raise AccessError("invalid_query", "Invalid lap identity")
    match = re.fullmatch(
        r"r:([0-9a-f]{32}(?:[0-9a-f]{32})?):([1-9][0-9]{0,8}):([0-9]{1,8})", value
    )
    if match:
        return match[1], int(match[2]), int(match[3])
    if re.fullmatch(r"[0-9a-f]{32}:l[1-9][0-9]{0,10}", value):
        return None
    raise AccessError("invalid_query", "Invalid lap identity")


class Service:
    def __init__(self, live):
        self.live = live
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=16)
        self._cache = collections.OrderedDict()
        self._versions = {}
        self._pending = set()
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name="engineer-comparison", daemon=True
        )
        self._worker.start()

    def snapshot(self):
        result = self.live.snapshot(compact=True)
        if result.get("current"):
            result["current"] = public_lap(result["current"], False)
        return result

    def history(self, since):
        result = self.live.history(since)
        if result["changed"]:
            result["laps"] = [public_lap(r, False) for r in result["laps"]]
            result["sessions"] = [
                project(s, ("id", "car_id", "track_id", "track", "laps"))
                for s in result["sessions"]
            ]
        return result

    def _detail(self, value, reference):
        chosen = selector(value)
        if chosen is None:
            return self.live.detail(value)
        source, session, lap = chosen
        path = reference(source)
        result = self.live.recording_detail(source, str(path), session, lap)
        if result.get("lap"):
            result["lap"].update(
                id=value,
                session_id=f"r:{source}:{session}",
                state="confirmed"
                if result["lap"].get("gt7_time_s") is not None
                else "unconfirmed",
                version=1,
            )
        return result

    def lap(self, value, reference):
        result = self._detail(value, reference)
        if result.get("lap"):
            result["lap"] = public_lap(result["lap"])
        return dict(version=1, generation=self.live.generation, **result)

    def reference(self, source, path):
        result = self.live.recording_snapshot(source, str(path))
        result = dict(result)
        result["laps"] = [
            public_lap(
                dict(
                    r,
                    id=f"r:{source}:{r['session']}:{r['lap']}",
                    session_id=f"r:{source}:{r['session']}",
                    state="confirmed"
                    if r.get("gt7_time_s") is not None
                    else "unconfirmed",
                    version=1,
                ),
                False,
            )
            for r in result["laps"]
        ]
        result["notes"] = notes(result.get("notes", []))
        return dict(version=1, generation=self.live.generation, **result)

    def compare(self, a, b, reference):
        left, right = self._detail(a, reference), self._detail(b, reference)
        for detail in (left, right):
            if detail["state"] != "ready":
                return dict(
                    version=1, generation=self.live.generation, state=detail["state"]
                )
        rows = left["lap"], right["lap"]
        # Content includes current observed trace, context and applied definition.
        key = hashlib.sha256(
            json.dumps(rows, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        content = key
        if any(row.get("state") == "provisional" for row in rows):
            # Measured prefixes grow, but semantic context and authorization must
            # still invalidate completed work. Keep one refresh per stable pair.
            stable = []
            for row in rows:
                if row.get("state") != "provisional":
                    stable.append(row)
                    continue
                fields = FIELDS - {
                    "version",
                    "elapsed_s",
                    "distance_m",
                    "length_m",
                    "fuel_used_l",
                    "top_speed_kmh",
                }
                if (
                    (row.get("track_definition") or {}).get("storage_source")
                    == "unsupported"
                ):
                    # Unsaved geometry proposals are derived from this growing
                    # prefix. They are measured content, not an external edit.
                    fields = fields - {"definition_fingerprint"}
                stable.append(
                    dict(
                        project(row, fields),
                        context_settings=row.get("context_settings", {}),
                        strategy=row.get("strategy", {}),
                        notes=row.get("notes", []),
                        rec=row.get("rec"),
                        session=row.get("session"),
                    )
                )
            key = hashlib.sha256(
                json.dumps(stable, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and self._versions.get(key) == content:
                return copy.deepcopy(self._cache[key])
            if key not in self._pending:
                try:
                    self._queue.put_nowait((key, content, rows))
                    self._pending.add(key)
                except queue.Full:
                    if cached is None:
                        return dict(
                            version=1,
                            generation=self.live.generation,
                            state="unavailable",
                        )
            if cached is not None:
                return copy.deepcopy(cached)
        return dict(version=1, generation=self.live.generation, state="pending")

    def _prepare(self, rows):
        a, b = rows
        return dict(
            version=1,
            generation=self.live.generation,
            state="ready",
            a=public_lap(a),
            b=public_lap(b),
            **gt7_comparison.compare(a, b),
        )

    def _remember(self, key, result, content=None):
        if len(json.dumps(result, allow_nan=False).encode()) > 2097152:
            result = dict(
                version=1, generation=self.live.generation, state="unavailable"
            )
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            self._versions[key] = content
            while (
                len(self._cache) > 16
                or sum(len(json.dumps(r).encode()) for r in self._cache.values())
                > 16777216
            ):
                old, _ = self._cache.popitem(last=False)
                self._versions.pop(old, None)

    def _run(self):
        while not self._stop.is_set():
            try:
                key, content, rows = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                result = self._prepare(rows)
                self._remember(key, result, content)
            except (ValueError, TypeError, KeyError, OSError):
                self._remember(
                    key,
                    dict(
                        version=1, generation=self.live.generation, state="unavailable"
                    ),
                    content,
                )
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._queue.task_done()

    def request(self, route, query, reference):
        allowed = {
            "snapshot": set(),
            "history": {"since"},
            "lap": {"id"},
            "reference": {"id"},
            "compare": {"a", "b"},
        }[route]
        query = {k: v for k, v in query.items() if k != "t"}
        if set(query) - allowed or any(len(v) != 1 or not v[0] for v in query.values()):
            raise AccessError(
                "invalid_query", "Unsupported or duplicate query parameter"
            )
        if route == "snapshot":
            return self.snapshot()
        if route == "history":
            since = query.get("since", [None])[0]
            if since is not None and not re.fullmatch(r"[0-9]{1,16}", since):
                raise AccessError("invalid_query", "Invalid history revision")
            return self.history(int(since) if since is not None else None)
        if set(query) != allowed:
            raise AccessError("invalid_query", "Required identity is missing")
        if route == "lap":
            return self.lap(query["id"][0], reference)
        if route == "reference":
            source = query["id"][0]
            if not re.fullmatch(r"[0-9a-f]{32}(?:[0-9a-f]{32})?", source):
                raise AccessError("invalid_query", "Invalid reference identity")
            return self.reference(source, reference(source))
        return self.compare(query["a"][0], query["b"][0], reference)

    def close(self):
        self._stop.set()
        self._worker.join(timeout=5)
