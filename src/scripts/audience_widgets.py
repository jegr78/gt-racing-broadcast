"""Shared audience definitions, durable baselines and normalized metric samples."""

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
import math
import html
import re
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid


@dataclass(frozen=True)
class Sample:
    value: float | None = None
    availability: str = "unknown"
    last_success: float | None = None
    exact: bool = True
    identity: str = ""
    supports_net: bool = True
    reason: str = ""
    age_seconds: float | None = None
    significant_digits: int | None = None


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def normalize(widget, sample=None, now=0, hold_seconds=120):
    manual = widget["source"] == "manual"
    if manual:
        sample = Sample(
            widget["manualValue"],
            "available",
            widget.get("updatedAt"),
            identity="manual:" + widget["id"],
        )
    sample = with_capabilities(
        sample or Sample(), widget["source"], widget.get("metric", "value")
    )
    status = (
        sample.availability
        if sample.availability in ("available", "stale", "unknown", "unavailable")
        else "unavailable"
    )
    raw = sample.value if finite(sample.value) else None
    available = status in ("available", "stale") and raw is not None
    reason = sample.reason
    hold = None if manual else 0
    if not available and not reason:
        reason = "No valid metric value available"
    if not manual and available:
        if not finite(sample.last_success):
            available = False
            status = "unknown"
            reason = "No successful metric update"
        else:
            age = sample.age_seconds
            if age is not None and (not finite(age) or age < 0):
                available = False
                status = "unknown"
                reason = "Invalid metric age"
                age = hold_seconds
            if age is None:
                age = max(0, now - sample.last_success)
            hold = max(0, hold_seconds - age)
            if available and age >= hold_seconds:
                available = False
                status = "stale"
                reason = "Last successful update is too old"
    source_available = available and status == "available"
    source_status = status
    value = raw if available else None
    if available and widget["mode"] == "net":
        baseline = widget.get("baseline")
        if not baseline or not finite(baseline.get("value")):
            available = False
            reason = "Capture a baseline before using a net goal"
        elif not sample.exact or not sample.supports_net:
            available = False
            reason = "This metric does not support an exact session gain"
        elif baseline.get("identity") != sample.identity:
            available = False
            reason = "Source changed; capture a new baseline"
        else:
            value = raw - baseline["value"]
            if not finite(value):
                available = False
                reason = "Net change is outside supported numeric range"
        if not available:
            status = "unavailable"
            value = None
    percent = None
    fill = None
    start = widget.get("start", 0)
    target = widget.get("target")
    if available and target is not None:
        if not finite(start) or not finite(target) or target <= start:
            available = False
            status = "unavailable"
            reason = "Invalid goal range"
            value = None
        else:
            with localcontext() as ctx:
                ctx.prec = 36
                ratio = (
                    (Decimal(str(value)) - Decimal(str(start)))
                    * 100
                    / (Decimal(str(target)) - Decimal(str(start)))
                )
                fill = float(max(Decimal(0), min(Decimal(100), ratio)))
                percent = float(ratio)
                if not finite(percent):
                    percent = None
    return {
        **{
            key: widget.get(key)
            for key in (
                "id",
                "label",
                "unit",
                "source",
                "metric",
                "mode",
                "start",
                "target",
                "decimals",
            )
        },
        "sourceAvailable": source_available,
        "sourceAvailability": source_status,
        "sourceIdentity": sample.identity,
        "supportsNet": sample.supports_net,
        "available": available,
        "value": value,
        "rawValue": raw,
        "percentage": percent,
        "fill": fill,
        "availability": status,
        "reason": reason,
        "holdFor": hold,
        "precision": {
            "exact": sample.exact,
            **(
                {"significantDigits": sample.significant_digits}
                if type(sample.significant_digits) is int
                and 1 <= sample.significant_digits <= 15
                else {}
            ),
        },
        "lastSuccess": sample.last_success if finite(sample.last_success) else None,
    }


ID_RE = re.compile(r"audience-[0-9a-f]{32}\Z")
METRICS = {
    "manual": ("value",),
    "youtube": ("likes", "viewers", "subscribers"),
    "twitch": ("viewers", "subscriptions"),
}
NET_METRICS = {("manual", "value"), ("youtube", "likes"), ("twitch", "subscriptions")}


def metric_capabilities(source, metric):
    rounded = source == "youtube" and metric == "subscribers"
    return {
        "exact": not rounded,
        "supportsNet": (source, metric) in NET_METRICS,
        **({"significantDigits": 3} if rounded else {}),
    }


def with_capabilities(sample, source, metric):
    info = metric_capabilities(source, metric)
    return replace(
        sample,
        exact=sample.exact and info["exact"],
        supports_net=sample.supports_net and info["supportsNet"],
        significant_digits=info.get("significantDigits", sample.significant_digits),
    )


FIELDS = frozenset((
    "id",
    "label",
    "unit",
    "source",
    "metric",
    "mode",
    "manualValue",
    "start",
    "target",
    "decimals",
    "baseline",
    "updatedAt",
))


def text(value, name, limit, required=False):
    if (
        not isinstance(value, str)
        or len(value.strip()) > limit
        or (required and not value.strip())
    ):
        raise ValueError("Invalid " + name)
    return value.strip()


def number(value, name):
    if not finite(value):
        raise ValueError("Invalid " + name)
    return value


def clean(raw, now=0):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ValueError("Invalid widget object")
    ident = raw.get("id")
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise ValueError("Invalid widget identity")
    label = text(raw.get("label"), "label", 120, True)
    unit = text(raw.get("unit", ""), "unit", 32)
    source = raw.get("source", "manual")
    metric = raw.get("metric", "value" if source == "manual" else None)
    if (
        not isinstance(source, str)
        or source not in METRICS
        or metric not in METRICS[source]
    ):
        raise ValueError("Invalid source metric")
    mode = raw.get("mode", "total")
    if mode not in ("total", "net") or (
        mode == "net" and (source, metric) not in NET_METRICS
    ):
        raise ValueError("Metric does not support this goal mode")
    value = number(raw.get("manualValue", 0), "manual value")
    start = number(raw.get("start", 0), "start")
    target = raw.get("target")
    if target is not None:
        target = number(target, "target")
        if target <= start:
            raise ValueError("Target must exceed start")
    decimals = raw.get("decimals", 0)
    if (
        isinstance(decimals, bool)
        or not isinstance(decimals, int)
        or not 0 <= decimals <= 6
    ):
        raise ValueError("Invalid display decimals")
    baseline = raw.get("baseline")
    if baseline is not None:
        if not isinstance(baseline, dict) or set(baseline) != {
            "value",
            "identity",
            "capturedAt",
        }:
            raise ValueError("Invalid baseline")
        baseline = {
            "value": number(baseline["value"], "baseline value"),
            "identity": text(baseline["identity"], "baseline identity", 256, True),
            "capturedAt": number(baseline["capturedAt"], "baseline time"),
        }
    return {
        "id": ident,
        "label": label,
        "unit": unit,
        "source": source,
        "metric": metric,
        "mode": mode,
        "manualValue": value,
        "start": start,
        "target": target,
        "decimals": decimals,
        "baseline": baseline,
        "updatedAt": number(raw.get("updatedAt", now), "update time"),
    }


MAX_WIDGETS = 32
_registry = {}
_registry_lock = threading.Lock()


def path_lock(path):
    key = os.path.normcase(os.path.realpath(path))
    with _registry_lock:
        return _registry.setdefault(key, threading.RLock())


class Store:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = path_lock(self.path)

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "widgets": []}
        with self.path.open(encoding="utf-8") as source:
            data = json.load(source)
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("widgets"), list)
        ):
            raise ValueError("Invalid audience file")
        if len(data["widgets"]) > MAX_WIDGETS:
            raise ValueError("Too many audience widgets")
        widgets = [clean(w) for w in data["widgets"]]
        if len({w["id"] for w in widgets}) != len(widgets):
            raise ValueError("Duplicate audience identity")
        return {"version": 1, "widgets": widgets}

    def read(self):
        with self.lock:
            return self._read()

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".audience-",
                suffix=".tmp",
                delete=False,
            ) as target:
                name = target.name
                json.dump(data, target, ensure_ascii=False, allow_nan=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.path)
        finally:
            if name:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass  # replaced temp file is already gone

    def _patch(self, fields):
        if not isinstance(fields, dict) or set(fields) - (
            FIELDS - {"id", "baseline", "updatedAt"}
        ):
            raise ValueError("Invalid audience edit")
        return fields

    def create(self, fields):
        with self.lock:
            data = self._read()
            if len(data["widgets"]) >= MAX_WIDGETS:
                raise ValueError("At most 32 audience widgets per profile")
            widget = clean({
                **self._patch(fields),
                "id": "audience-" + uuid.uuid4().hex,
                "updatedAt": self.clock(),
            })
            data["widgets"].append(widget)
            self._write(data)
            return widget

    def _index(self, data, ident):
        for i, w in enumerate(data["widgets"]):
            if w["id"] == ident:
                return i
        raise ValueError("Unknown audience identity")

    def update(self, ident, fields):
        with self.lock:
            data = self._read()
            i = self._index(data, ident)
            widget = clean({
                **data["widgets"][i],
                **self._patch(fields),
                "updatedAt": self.clock(),
            })
            data["widgets"][i] = widget
            self._write(data)
            return widget

    def reset(self, ident):
        with self.lock:
            data = self._read()
            i = self._index(data, ident)
            widget = data["widgets"][i]
            if widget["source"] != "manual":
                raise ValueError("Automatic values require a current source sample")
            if widget["mode"] == "net":
                widget["baseline"] = {
                    "value": widget["manualValue"],
                    "identity": "manual:" + ident,
                    "capturedAt": self.clock(),
                }
            else:
                widget["manualValue"] = widget["start"]
                widget["baseline"] = None
            widget = clean({**widget, "updatedAt": self.clock()})
            data["widgets"][i] = widget
            self._write(data)
            return widget

    def delete(self, ident):
        with self.lock:
            data = self._read()
            del data["widgets"][self._index(data, ident)]
            self._write(data)

    def capture(self, ident, sample=None, expected_source=None):
        with self.lock:
            data = self._read()
            index = self._index(data, ident)
            widget = data["widgets"][index]
            if expected_source is not None and (widget["source"], widget["metric"]) != expected_source:
                raise ValueError("Audience source changed; capture the current source again")
            if widget["mode"] != "net":
                raise ValueError("Choose net mode before capturing a baseline")
            observed = sample() if callable(sample) else sample
            normalized = normalize(widget, observed, now=self.clock())
            if (
                not normalized["sourceAvailable"]
                or not normalized["precision"]["exact"]
                or not normalized["supportsNet"]
                or not normalized["sourceIdentity"]
            ):
                raise ValueError("Baseline needs a fresh, exact source value")
            widget["baseline"] = {
                "value": normalized["rawValue"],
                "identity": normalized["sourceIdentity"],
                "capturedAt": self.clock(),
            }
            widget["updatedAt"] = self.clock()
            data["widgets"][index] = clean(widget)
            self._write(data)
            return data["widgets"][index]


class MetricCache:
    """One relay-owned snapshot per metric; monotonic observation bounds freshness."""

    def __init__(self, clock=time.time, monotonic=time.monotonic):
        self.clock = clock
        self.monotonic = monotonic
        self._samples = {}
        self._lock = threading.RLock()

    def publish(self, source, metric, sample):
        if not isinstance(sample, Sample):
            raise ValueError("Invalid metric sample")
        observed = self.monotonic()
        age = sample.age_seconds
        if age is not None and (not finite(age) or age < 0):
            raise ValueError("Invalid metric age")
        if age is None:
            age = (
                max(0, self.clock() - sample.last_success)
                if finite(sample.last_success)
                else 0
            )
        with self._lock:
            self._samples[(source, metric)] = (sample, observed - age)

    def fail(self, source, metric, reason):
        with self._lock:
            sample, observed = self._samples.get(
                (source, metric), (Sample(), self.monotonic())
            )
            status = (
                "stale"
                if sample.availability in ("available", "stale")
                else sample.availability
            )
            self._samples[(source, metric)] = (
                replace(sample, availability=status, reason=reason),
                observed,
            )

    def read(self, source, metric):
        with self._lock:
            row = self._samples.get((source, metric))
            if row is None:
                sample = Sample(reason="Provider not connected")
            else:
                sample, observed = row
                sample = replace(
                    sample, age_seconds=max(0, self.monotonic() - observed)
                )
            return with_capabilities(sample, source, metric)


class Runtime:
    def __init__(self, path, cache=None, clock=time.time):
        self.store = Store(path, clock=clock) if path else None
        self.cache = cache or MetricCache(clock=clock)
        self.clock = clock

    def data(self):
        try:
            definitions = self.store.read()["widgets"] if self.store else []
            return {
                "ok": True,
                "widgets": [
                    normalize(
                        widget,
                        self.cache.read(widget["source"], widget["metric"]),
                        now=self.clock(),
                    )
                    for widget in definitions
                ],
            }
        except (OSError, ValueError):
            return {"ok": False, "error": "Could not read audience configuration"}


WIDGET_PROPS = (
    "left",
    "top",
    "width",
    "height",
    "padding",
    "fontSize",
    "fontFamily",
    "fontWeight",
    "fontStyle",
    "color",
    "background",
    "borderWidth",
    "borderStyle",
    "borderColor",
    "borderRadius",
    "opacity",
    "rotation",
    "visible",
    "presentation",
    "showNumber",
    "showPercent",
    "barColor",
    "trackColor",
)
WIDGET_CSS = """
.audience-widget{position:absolute;box-sizing:border-box;left:1400px;top:70px;
width:420px;height:140px;padding:16px;z-index:10;font:24px/1.2 sans-serif;
color:#fff;background:rgba(0,0,0,.8);border:2px solid #27d34d;border-radius:12px}
.audience-widget[hidden],.audience-widget [hidden]{display:none!important}
.audience-label{display:block;font-size:.7em;overflow-wrap:anywhere}
.audience-number{display:var(--audience-number,block);font-size:1.5em}
.audience-target{display:var(--audience-target,inline);font-size:.6em}
.audience-track{display:var(--audience-track,block);height:12px;margin-top:8px;
background:var(--audience-track-color,#27412c);border-radius:8px;overflow:hidden}
.audience-fill{height:100%;background:var(--audience-bar-color,#27d34d)}
.audience-percent-wrap{display:var(--audience-percent-mode,block)}
.audience-percent{display:var(--audience-percent,block);font-size:.6em;text-align:right}
"""


def markup(widget):
    ident = widget["id"]
    if not ID_RE.fullmatch(ident):
        raise ValueError("Invalid widget identity")
    label = html.escape(widget["label"], quote=True)
    props = ",".join(WIDGET_PROPS)
    return (
        f'<div id="{ident}" class="audience-widget" data-audience-widget '
        f'data-edit="{label}" data-edit-props="{props}" hidden>'
        '<span class="audience-label"></span><div class="audience-number">'
        '<span class="audience-value"></span> <span class="audience-unit"></span>'
        '<span class="audience-target"></span></div>'
        '<div class="audience-track"><div class="audience-fill"></div></div>'
        '<div class="audience-percent-wrap"><span class="audience-percent"></span></div></div>'
    )


def enhance_html(base, definitions, placements, live=False):
    selected = [widget for widget in definitions if widget["id"] in placements]
    if not selected:
        return base
    base = base.replace("</style>", WIDGET_CSS + "</style>", 1)
    elements = "".join(markup(widget) for widget in selected)
    script = '<script src="/audience.js" data-audience-live></script>' if live else ""
    marker = "<script" if "<script" in base else "</body>"
    return base.replace(marker, elements + script + marker, 1)


def live_html(base, overlay_dir, page):
    """Freeze placement at page load; shared data edits update existing nodes only."""
    if not overlay_dir:
        return base
    try:
        directory = Path(overlay_dir)
        definitions = Store(directory.parent / "audience.json").read()["widgets"]
        path = directory / ("layout-" + page + ".json")
        if not path.is_file():
            return base
        layout = json.loads(path.read_text(encoding="utf-8"))
        return enhance_html(base, definitions, layout.get("slots", {}), live=True)
    except (OSError, ValueError, TypeError, AttributeError):
        return base  # malformed audience state must never interrupt the race overlay
