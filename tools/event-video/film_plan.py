"""The frame plan of the event walkthrough film (#1312), pure and without I/O.

A record run's timeline.json says when each step ran and when each recording started,
in seconds on the engine clock. The plan maps every output frame to a source time
(pace), to the views shown large (focus) and to the rectangle, ring and recording time
of every view on the 2560x1440 stage, plus the caption, chapter and clock it shows."""
import bisect
import html
import json
import math
import os
from dataclasses import dataclass

CANVAS = (2560, 1440)
FPS = 30
LEAD_IN_S = 1.5          # a captioned step's view is large and still before it acts
HOLD_S = 3.0             # and stays in real time this long after its action returned
WAIT_SPEED = 8.0
MIN_WAIT_S = 4.0         # a shorter wait plays in real time, so the fast-forward mark does not flicker
MOVE_S = 0.6
CHAPTER_FRESH_S = 4.0

MARGIN = 16
TOPBAR_H = 88
CAPTION_H = 96
RAIL_MAX_W = 360
TAB_LARGE = 40           # the role label sits in a tab above the picture, never on it
TAB_RAIL = 26
TOPBAR_RECT = (0, 0, CANVAS[0], TOPBAR_H)
CAPTION_RECT = (MARGIN, CANVAS[1] - MARGIN - CAPTION_H, CANVAS[0] - 2 * MARGIN, CAPTION_H)

PROGRAM = "program"
COMMENTATOR = "commentator:"
STAGE_ORDER = ("program", "director", "race_control", COMMENTATOR, "viewer", "driver", "producer")
LABELS = {"program": "Program", "director": "Director Panel", "race_control": "Race Control",
          "viewer": "Public timing", "driver": "Driver", "producer": "Control Center", "drivers": "Drivers"}


def label(role):
    """The name a role has on the stage, None for a role nobody sees (api)."""
    if role.startswith(COMMENTATOR):
        return "Cockpit " + role[len(COMMENTATOR):]
    return LABELS.get(role)


def _stage_rank(view):
    for n, key in enumerate(STAGE_ORDER):
        if view == key or (key == COMMENTATOR and view.startswith(COMMENTATOR)):
            return (n, view)
    return None


def stage_views(recordings):
    """The recorded views on the stage, in stage order. The overlay page is left out:
    the program picture already shows it."""
    views = {r["view"] for r in recordings if r.get("file")}
    return sorted((v for v in views if _stage_rank(v) is not None), key=_stage_rank)


def load_timeline(directory):
    with open(os.path.join(directory, "timeline.json"), encoding="utf-8") as fh:
        return json.load(fh)


@dataclass(frozen=True)
class Segment:
    a: float          # source start
    b: float          # source end
    speed: float
    out: float        # output time at a


@dataclass(frozen=True)
class Layout:
    large: tuple
    quarters: bool = False


def pace(steps, t0, t1, lead_in=LEAD_IN_S, hold=HOLD_S, speed=WAIT_SPEED, min_wait=MIN_WAIT_S):
    """Contiguous segments from t0 to t1: each captioned step's [start - lead_in,
    acted + hold] in real time, every other stretch sped up by `speed`, unless it is
    shorter than `min_wait`."""
    windows = sorted((max(t0, s["start"] - lead_in), min(t1, s["acted"] + hold))
                     for s in steps if s.get("caption"))
    real = []
    for a, b in windows:
        if b <= a:
            continue
        if real and a <= real[-1][1]:
            real[-1][1] = max(real[-1][1], b)
        else:
            real.append([a, b])
    parts, at = [], t0
    for a, b in real:
        if a > at:
            parts.append((at, a, 1.0 if a - at < min_wait else speed))
        parts.append((a, b, 1.0))
        at = b
    if t1 > at:
        parts.append((at, t1, 1.0 if t1 - at < min_wait else speed))
    merged = []
    for a, b, v in parts:
        if merged and merged[-1][2] == v:
            merged[-1][1] = b
        else:
            merged.append([a, b, v])
    out, segs = 0.0, []
    for a, b, v in merged:
        segs.append(Segment(a, b, v, out))
        out += (b - a) / v
    return segs


def focus_layout(step, prev, views):
    """The large views from a captioned step on: its focus, else its own role next to the
    previous first view; a step on no view keeps the previous layout."""
    named = step.get("focus") or []
    shown = [v for v in named if v in views]
    if len(named) == 4 and len(shown) == 4:
        return Layout(tuple(shown), True)
    if shown:
        return Layout(tuple(shown[:2]))
    role = step["role"]
    if role in views:
        return Layout((role,) + tuple(v for v in prev.large if v != role)[:1])
    return prev


def label_rect(rect):
    """The tab above a view's picture that holds its role label."""
    x, y, w, _h, _opacity, tab = rect
    return (x, y - tab, w, tab)


def _fit_below_tab(slot, size, tab):
    x, y, w, h = slot
    return _fit((x, y + tab, w, h - tab), size)


def _fit(slot, size):
    x, y, w, h = slot
    aw, ah = size
    scale = min(w / aw, h / ah)
    fw, fh = aw * scale, ah * scale
    return (x + (w - fw) / 2, y + (h - fh) / 2, fw, fh)


def _columns(area, n):
    x, y, w, h = area
    cw = (w - (n - 1) * MARGIN) / n
    return [(x + i * (cw + MARGIN), y, cw, h) for i in range(n)]


def rail_cell(count):
    """The size of a rail preview when `count` views share the rail."""
    n = max(1, count)
    w = min(RAIL_MAX_W, (CANVAS[0] - 2 * MARGIN - (n - 1) * MARGIN) / n)
    return w, w * 10 / 16


def target_rects(views, layout, sizes):
    """{view: (x, y, w, h, opacity, tab)} of a layout at rest: the picture's rect, and the
    height of the label tab above it."""
    W = CANVAS[0]
    cap_y = CAPTION_RECT[1]
    top = TOPBAR_H + MARGIN
    cw, ch = rail_cell(len(views) - 1)
    rail_y = cap_y - MARGIN - ch - TAB_RAIL
    rects = {}
    if layout.quarters:
        area = (MARGIN, top, W - 2 * MARGIN, cap_y - MARGIN - top)
        qh = (area[3] - MARGIN) / 2
        cols = _columns((area[0], area[1], area[2], qh), 2)
        slots = cols + [(x, y + qh + MARGIN, w, h) for x, y, w, h in cols]
    else:
        area = (MARGIN, top, W - 2 * MARGIN, rail_y - MARGIN - top)
        slots = _columns(area, len(layout.large))
    for view, slot in zip(layout.large, slots, strict=False):
        rects[view] = (*_fit_below_tab(slot, sizes[view], TAB_LARGE), 1.0, TAB_LARGE)
    rail = [v for v in views if v not in layout.large]
    x0 = (W - (len(rail) * cw + (len(rail) - 1) * MARGIN)) / 2
    for n, view in enumerate(rail):
        cell = (x0 + n * (cw + MARGIN), rail_y, cw, ch + TAB_RAIL)
        rects[view] = (*_fit_below_tab(cell, sizes[view], TAB_RAIL), 0.0 if layout.quarters else 1.0, TAB_RAIL)
    return rects


def _ease(p):
    return p * p * (3 - 2 * p)


def _lerp(a, b, p):
    return tuple(x + (y - x) * p for x, y in zip(a, b, strict=True))


def clock(seconds):
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _stamp(seconds, hours):
    s = int(seconds)
    if hours:
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def chapters_txt(chapters):
    """`0:00 Title` per chapter, the form video platforms read; h:mm:ss past an hour."""
    hours = any(t >= 3600 for t, _ in chapters)
    return "".join(f"{_stamp(t, hours)} {title}\n" for t, title in chapters)


def _vtt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def captions_vtt(cues):
    out = ["WEBVTT", ""]
    for n, (a, b, _who, text) in enumerate(cues, 1):
        out += [str(n), f"{_vtt_time(a)} --> {_vtt_time(b)}", html.escape(text, quote=False), ""]
    return "\n".join(out)


class Plan:
    """The film of one timeline: `frames` output frames at `fps`. `available` limits the
    recordings shown to those indexes, the ones transcoded; None shows every one."""

    def __init__(self, timeline, fps=FPS, lead_in=LEAD_IN_S, hold=HOLD_S, wait_speed=WAIT_SPEED,
                 min_wait=MIN_WAIT_S, durations=None, available=None):
        steps = [s for s in timeline.get("steps") or [] if s.get("start") is not None]
        if not steps:
            raise ValueError("the timeline has no step")
        self.fps, self.lead_in, self.hold, self.wait_speed = fps, lead_in, hold, wait_speed
        self.steps = sorted(steps, key=lambda s: s["start"])
        for s in self.steps:
            s.setdefault("acted", s["start"])
            s.setdefault("end", s["acted"])
        self.recordings = list(timeline.get("recordings") or [])
        self.durations = durations or {}
        self.available = available
        self.views = stage_views(self.recordings)
        if not self.views:
            raise ValueError("the timeline has no recording on the stage")
        self.t0 = self.steps[0]["start"]
        self.t1 = max(s["end"] for s in self.steps)
        if self.t1 <= self.t0:
            raise ValueError("the timeline's steps last no time")
        self.segments = pace(self.steps, self.t0, self.t1, lead_in, hold, wait_speed, min_wait)
        self._seg_starts = [s.a for s in self.segments]
        self._seg_outs = [s.out for s in self.segments]
        last = self.segments[-1]
        self.duration = last.out + (last.b - last.a) / last.speed
        self.frames = max(1, math.ceil(self.duration * fps - 1e-9))
        self.sizes = self._sizes()
        self._by_view = self._recordings_by_view()
        self._shown = self._shown_from()
        self._switches = self._layout_switches()
        self._rings = self._ring_windows()
        self._cues = self._make_cues()
        self._chapters = self._make_chapters()

    def _sizes(self):
        sizes = {}
        for r in self.recordings:
            size = r.get("size") or {}
            if r["view"] in self.views and r["view"] not in sizes and size.get("width") and size.get("height"):
                sizes[r["view"]] = (size["width"], size["height"])
        for v in self.views:
            sizes.setdefault(v, (1920, 1080) if v == PROGRAM else (1440, 900))
        return sizes

    def _recordings_by_view(self):
        by = {v: [] for v in self.views}
        for n, r in enumerate(self.recordings):
            if r.get("file") and r["view"] in by and (self.available is None or n in self.available):
                by[r["view"]].append((r["start"], n))
        return {v: sorted(lst) for v, lst in by.items()}

    # time

    def _seg(self, s):
        return self.segments[max(0, bisect.bisect_right(self._seg_starts, s) - 1)]

    def speed_at(self, s):
        return self._seg(s).speed

    def out_time(self, s):
        seg = self._seg(min(max(s, self.t0), self.t1))
        return seg.out + (min(max(s, self.t0), self.t1) - seg.a) / seg.speed

    def src_time(self, t):
        seg = self.segments[max(0, bisect.bisect_right(self._seg_outs, t) - 1)]
        return min(self.t1, seg.a + (t - seg.out) * seg.speed)

    def frame_range(self, start=None, stop=None):
        """The frames [a, b) of film seconds start..stop, clipped to the film."""
        a = 0 if start is None else max(0, min(self.frames, round(start * self.fps)))
        b = self.frames if stop is None else max(0, min(self.frames, round(stop * self.fps)))
        if b <= a:
            raise ValueError(f"no frame between {start} and {stop} s of a {self.duration:.1f} s film")
        return a, b

    # layout

    def _shown_from(self):
        """{step id: source time its caption and layout start}: the lead-in, but not before
        the previous captioned step has started its hold or the step itself starts."""
        shown, prev = {}, None
        for s in self.steps:
            if not s.get("caption"):
                continue
            a = s["start"] - self.lead_in
            if prev is not None:
                a = max(a, min(prev["acted"] + self.hold, s["start"]))
            shown[s["id"]] = a
            prev = s
        return shown

    def _layout_switches(self):
        """[(output time, Layout, from rects)]: the layout each captioned step sets, at its lead-in."""
        first = next((s for s in self.steps if s.get("caption")), None)
        layout = focus_layout(first, Layout(()), self.views) if first is not None else Layout(())
        if not layout.large:
            layout = Layout((self.views[0],))
        rects = target_rects(self.views, layout, self.sizes)
        switches = [(0.0, layout, rects)]
        for s in self.steps:
            if not s.get("caption"):
                continue
            new = focus_layout(s, layout, self.views)
            if new == layout:
                continue
            t = max(switches[-1][0], self.out_time(self._shown[s["id"]]))
            switches.append((t, new, self._rects_at(switches, t)[0]))
            layout = new
        return switches

    def _rects_at(self, switches, t):
        k = max(0, bisect.bisect_right([sw[0] for sw in switches], t) - 1)
        at, layout, start = switches[k]
        goal = target_rects(self.views, layout, self.sizes)
        p = _ease(min(1.0, (t - at) / MOVE_S)) if MOVE_S > 0 else 1.0
        return {v: _lerp(start[v], goal[v], p) for v in self.views}, layout, p < 1.0

    def _ring_windows(self):
        rings = {v: [] for v in self.views}
        for s in self.steps:
            if s["role"] in rings:
                rings[s["role"]].append((s["start"], s["acted"] + self.hold))
        return rings

    # texts

    def _make_cues(self):
        rows = [(self.out_time(self._shown[s["id"]]), self.out_time(s["acted"] + self.hold),
                 label(s["role"]), s["caption"]) for s in self.steps if s.get("caption")]
        cues = []
        for n, (a, b, who, text) in enumerate(rows):
            if n + 1 < len(rows):
                b = min(b, rows[n + 1][0])
            if b > a:
                cues.append((a, b, who, text))
        return cues

    def _make_chapters(self):
        rows = [(self.out_time(self._shown.get(s["id"], s["start"] - self.lead_in)), s["chapter"])
                for s in self.steps if s.get("chapter")]
        if rows:
            rows[0] = (0.0, rows[0][1])
        return rows

    def cues(self):
        return list(self._cues)

    def chapters(self):
        return list(self._chapters)

    # frames

    def _source(self, view, s):
        recs = self._by_view[view]
        k = bisect.bisect_right([start for start, _ in recs], s) - 1
        if k < 0:
            return None
        start, n = recs[k]
        t = s - start
        dur = self.durations.get(self.recordings[n]["file"])
        if dur is not None:
            t = min(t, max(0.0, dur - 1.0 / self.fps))
        return [n, round(t, 4)]

    def frame(self, i):
        """The stage state of output frame i."""
        t = i / self.fps
        s = self.src_time(t)
        speed = self.speed_at(s)
        rects, layout, moving = self._rects_at(self._switches, t)
        cue = next((c for c in self._cues if c[0] <= t < c[1]), None)
        chapter = None
        for k, (ct, title) in enumerate(self._chapters):
            if ct <= t:
                chapter = (k, ct, title)
        rings = [v for v, ws in self._rings.items() if any(a <= s <= b for a, b in ws)]
        return {
            "i": i, "t": s, "speed": speed, "ff": f"{speed:g}x" if speed > 1 else "",
            "large": list(layout.large), "quarters": layout.quarters, "moving": moving,
            "rects": {v: [round(x, 2) for x in r] for v, r in rects.items()},
            "rings": rings, "sources": {v: self._source(v, s) for v in self.views},
            "caption": cue[3] if cue else None, "who": cue[2] if cue else None,
            "chapter": chapter[2] if chapter else None,
            "chapter_no": chapter[0] + 1 if chapter else 0,
            "chapter_fresh": chapter is not None and t - chapter[1] < CHAPTER_FRESH_S,
            "clock": clock(s),
        }

    def stage_config(self, urls):
        """What the stage page's setup() needs: views with labels, and the recordings with
        the URL each is served at ({recording index: url})."""
        return {"canvas": list(CANVAS), "caption": list(CAPTION_RECT), "topbar": list(TOPBAR_RECT),
                "views": [{"id": v, "label": label(v) or v} for v in self.views],
                "recordings": [{"index": n, "view": r["view"], "url": urls.get(n)}
                               for n, r in enumerate(self.recordings) if r["view"] in self.views and r.get("file")]}
