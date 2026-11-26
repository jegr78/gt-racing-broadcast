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
LEAD_IN_S = 2.5          # a captioned step's view is large and still before it acts
HOLD_S = 3.0             # and stays this long after its action returned
MIN_CAPTION_S = 6.0
MAX_ACTION_S = 8.0       # a longer action outside a swap chapter plays this much in real time, the rest sped up
RUN_SHOWN = 3            # of a run of steps with the same role and action, the later ones play sped up
SWAP_HOLD_S = 6.0        # the hold of each step in a chapter with a four-view switch
WAIT_SPEED = 8.0
MIN_WAIT_S = 4.0         # a shorter wait plays in real time, so the fast-forward mark does not flicker
MOVE_S = 0.6
CHAPTER_FRESH_S = 4.0
FRONT_SHARE = 0.8        # a single large view comes forward at this share of the canvas
DIM = 0.35               # the opacity of the stage behind it

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
    if _tab_of(role):
        return f"{LABELS[_tab_of(role)]}: {role.split(':', 1)[1]}"
    return LABELS.get(role)


def _tab_of(view):
    """The role whose page opened tab `view` (`<role>:<tab>`), None for any other view."""
    role = view.split(":", 1)[0]
    return role if ":" in view and not view.startswith(COMMENTATOR) and role in STAGE_ORDER else None


def _stage_rank(view):
    for n, key in enumerate(STAGE_ORDER):
        if view == key or (key == COMMENTATOR and view.startswith(COMMENTATOR)) or _tab_of(view) == key:
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
    b: float          # source end, equal to a in a still
    speed: float      # 0 in a still
    out: float        # output time at a
    dur: float        # output duration


@dataclass(frozen=True)
class Layout:
    large: tuple
    quarters: bool = False
    behind: tuple = ()   # with one large view: the views large behind it, dimmed


def beats(steps, lead_in=LEAD_IN_S, hold=HOLD_S):
    """{step id: (lead-in, real-time share of the action, hold, minimum time on screen, id of
    the first step of its sped-up run tail or None)} of each captioned step."""
    captioned = [s for s in steps if s.get("caption")]
    swap_chapters, chapter, chapter_of = set(), None, {}
    for s in steps:
        chapter = s.get("chapter") or chapter
        chapter_of[s["id"]] = chapter
        if s.get("caption") and len(s.get("focus") or ()) == 4:
            swap_chapters.add(chapter)
    out, run, tail = {}, 0, None
    for n, s in enumerate(captioned):
        prev = captioned[n - 1] if n else None
        chapter = chapter_of[s["id"]]
        swap = chapter is not None and chapter in swap_chapters
        run = run + 1 if prev and (prev["role"], prev.get("action")) == (s["role"], s.get("action")) else 0
        if run >= RUN_SHOWN and not swap:
            tail = tail if run > RUN_SHOWN else s["id"]
            out[s["id"]] = (0.0, 0.0, 0.0, 0.0, tail)
            continue
        h = SWAP_HOLD_S if swap else hold
        if len(s.get("focus") or ()) == 4:
            h = max(h, s["end"] - s["acted"] + hold)
        out[s["id"]] = (lead_in, math.inf if swap else MAX_ACTION_S, h, MIN_CAPTION_S, None)
    return out


def pace(steps, t0, t1, lead_in=LEAD_IN_S, hold=HOLD_S, speed=WAIT_SPEED, min_wait=MIN_WAIT_S):
    """(segments, marks): contiguous segments from t0 to t1 and, per captioned step, the
    output times its caption starts (shown), its action starts (act) and its caption ends. A captioned step plays its lead-in, action
    and hold in real time; where the source has no time for them (the next step follows at
    once), the picture stands still. Every other stretch is sped up by `speed`, unless it
    is shorter than `min_wait`."""
    captioned = [s for s in steps if s.get("caption")]
    params = beats(steps, lead_in, hold)
    shown, prev = [], None
    for s in captioned:
        lead = params[s["id"]][0]
        a = max(t0, s["start"] - lead)
        if prev is not None:
            a = max(a, min(prev["acted"] + params[prev["id"]][2], s["start"]), shown[-1])
        shown.append(a)
        prev = s
    real, events, index = [], [], {s["id"]: n for n, s in enumerate(captioned)}
    for n, s in enumerate(captioned):
        lead, cap, h, least, tail = params[s["id"]]
        nxt = shown[n + 1] if n + 1 < len(captioned) else t1
        e = max(s["acted"], min(s["acted"] + h, nxt, t1))
        real.append((shown[n], min(s["acted"], s["start"] + cap)))
        real.append((s["acted"], e))
        lead_still = max(0.0, lead - (s["start"] - shown[n]))
        act = min(s["acted"] - s["start"], cap) + max(0.0, s["acted"] - s["start"] - cap) / speed
        seen = (s["start"] - shown[n]) + lead_still + act + (e - s["acted"])
        hold_still = max(0.0, h - (e - s["acted"]), least - seen)
        last_of_tail = tail and (n + 1 == len(captioned) or params[captioned[n + 1]["id"]][4] != tail)
        if last_of_tail:
            hold_still = max(0.0, MIN_CAPTION_S - (e - shown[index[tail]]) / speed)
        events += [(shown[n], (n, 0), "shown", 0.0), (s["start"], (n, 1), "still", lead_still),
                   (s["start"], (n, 2), "act", 0.0), (e, (n, 3), "still", hold_still), (e, (n, 4), "end", 0.0)]
    merged = []
    for a, b in sorted(real):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    parts, at = [], t0
    for a, b in merged:
        if a > at:
            parts.append((at, a, 1.0 if a - at < min_wait else speed))
        parts.append((a, b, 1.0))
        at = b
    if t1 > at:
        parts.append((at, t1, 1.0 if t1 - at < min_wait else speed))
    events.sort(key=lambda e: (e[0], e[1]))
    out, segs, marks, k = 0.0, [], {}, 0
    for a, b, v in parts:
        cur = a
        while k < len(events) and events[k][0] <= b:
            p, (n, _), kind, dur = events[k]
            k += 1
            if p > cur:
                segs.append(Segment(cur, p, v, out, (p - cur) / v))
                out += (p - cur) / v
                cur = p
            if kind == "still" and dur > 0:
                segs.append(Segment(cur, cur, 0.0, out, dur))
                out += dur
            elif kind != "still":
                marks.setdefault(captioned[n]["id"], {})[kind] = out
        if b > cur:
            segs.append(Segment(cur, b, v, out, (b - cur) / v))
            out += (b - cur) / v
    return segs, marks


def focus_layout(step, prev, views):
    """The large views from a captioned step on: its focus, else its own role alone; a step
    on no view keeps the previous layout. `views` are the views on the stage. A single large
    view comes forward over the views large before."""
    named = step.get("focus") or []
    shown = [v for v in named if v in views]
    if len(named) == 4 and len(shown) == 4:
        return Layout(tuple(shown), True)
    prev_large = tuple(v for v in prev.large if v in views)
    if shown:
        large = tuple(shown[:2])
    elif step["role"] in views:
        large = (step["role"],)
    elif prev_large == prev.large:
        return Layout(prev.large, prev.quarters, tuple(v for v in prev.behind if v in views))
    else:
        large = prev_large[:2] or tuple(views[:1])
    if len(large) != 1:
        return Layout(large)
    before = prev.large + prev.behind if len(prev.large) == 1 and not prev.quarters else prev.large
    return Layout(large, behind=tuple(v for v in before if v in views and v != large[0])[:2])


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


def front_rect(size):
    """The rect of a view that comes forward: centred, FRONT_SHARE of the canvas, below its tab
    and clear of the chapter bar and the caption line."""
    top = TOPBAR_H + MARGIN + TAB_LARGE
    band = CAPTION_RECT[1] - MARGIN - top
    w, h = CANVAS[0] * FRONT_SHARE, min(CANVAS[1] * FRONT_SHARE, band)
    return _fit(((CANVAS[0] - w) / 2, top + (band - h) / 2, w, h), size)


def target_rects(views, layout, sizes):
    """{view: (x, y, w, h, opacity, tab)} of a layout at rest: the picture's rect, and the
    height of the label tab above it."""
    if len(layout.large) == 1 and not layout.quarters and layout.large[0] in views:
        front = layout.large[0]
        rest = [v for v in views if v != front]
        rects = {v: (*r[:4], r[4] * DIM, r[5]) for v, r in _plain(rest, Layout(layout.behind), sizes).items()}
        rects[front] = (*front_rect(sizes[front]), 1.0, TAB_LARGE)
        return rects
    return _plain(views, layout, sizes)


def _plain(views, layout, sizes):
    layout = Layout(tuple(v for v in layout.large if v in views), layout.quarters)
    W = CANVAS[0]
    cap_y = CAPTION_RECT[1]
    top = TOPBAR_H + MARGIN
    rail = [v for v in views if v not in layout.large]
    cw, ch = rail_cell(max(len(views) - 1, len(rail)))
    rail_y = cap_y - MARGIN - ch - TAB_RAIL
    rects = {}
    if layout.quarters:
        area = (MARGIN, top, W - 2 * MARGIN, cap_y - MARGIN - top)
        qh = (area[3] - MARGIN) / 2
        cols = _columns((area[0], area[1], area[2], qh), 2)
        slots = cols + [(x, y + qh + MARGIN, w, h) for x, y, w, h in cols]
    else:
        area = (MARGIN, top, W - 2 * MARGIN, rail_y - MARGIN - top)
        slots = _columns(area, len(layout.large)) if layout.large else []
    for view, slot in zip(layout.large, slots, strict=False):
        rects[view] = (*_fit_below_tab(slot, sizes[view], TAB_LARGE), 1.0, TAB_LARGE)
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
        self.t0 = max(self.steps[0]["start"], self._first_view_opens())
        self.t1 = max(s["end"] for s in self.steps)
        if self.t1 <= self.t0:
            raise ValueError("the timeline's steps last no time")
        self.segments, self._marks = pace(self.steps, self.t0, self.t1, lead_in, hold, wait_speed, min_wait)
        self._seg_ends = [s.b for s in self.segments]
        self._seg_outs = [s.out for s in self.segments]
        self._moving = [s for s in self.segments if s.speed]
        last = self.segments[-1]
        self.duration = last.out + last.dur
        self.frames = max(1, math.ceil(self.duration * fps - 1e-9))
        self.sizes = self._sizes()
        self._by_view = self._recordings_by_view()
        self._present = self._presence()
        self._switches = self._layout_switches()
        self._rings = self._ring_windows()
        self._cues = self._make_cues()
        self._chapters = self._make_chapters()

    def _first_view_opens(self):
        """When the views of the first caption have all opened, so the film opens on them."""
        first = next((s for s in self.steps if s.get("caption")), None)
        if first is None:
            return -math.inf
        named = [v for v in first.get("focus") or [] if v in self.views] or [first["role"]]
        opens = [min(r["start"] for r in self.recordings if r["view"] == v and r.get("file"))
                 for v in named if v in self.views]
        return max(opens, default=-math.inf)

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

    def speed_at(self, s):
        """The speed source time s plays at, outside a still."""
        k = bisect.bisect_right([seg.a for seg in self._moving], s) - 1
        return self._moving[max(0, k)].speed

    def out_time(self, s):
        """The first output time that shows source time s."""
        s = min(max(s, self.t0), self.t1)
        seg = self.segments[min(len(self.segments) - 1, bisect.bisect_left(self._seg_ends, s))]
        return seg.out + ((s - seg.a) / seg.speed if seg.speed else 0.0)

    def _out_seg(self, t):
        return self.segments[max(0, bisect.bisect_right(self._seg_outs, t) - 1)]

    def src_time(self, t):
        seg = self._out_seg(t)
        return min(self.t1, seg.a + (t - seg.out) * seg.speed)

    def frame_range(self, start=None, stop=None):
        """The frames [a, b) of film seconds start..stop, clipped to the film."""
        a = 0 if start is None else max(0, min(self.frames, round(start * self.fps)))
        b = self.frames if stop is None else max(0, min(self.frames, round(stop * self.fps)))
        if b <= a:
            raise ValueError(f"no frame between {start} and {stop} s of a {self.duration:.1f} s film")
        return a, b

    # layout

    def _presence(self):
        """[views the captions place on the stage] per captioned step. A commentator cockpit
        comes on with the chapter of the first caption that names it. At a four-view switch
        the cockpit on air before it (at the first switch: the one named first) hands over and
        leaves with the next caption; one named
        again later comes back with that chapter. A cockpit no caption names stays, and so
        does the one on air at the end. A tab a page opened is placed only by the captions
        that name it."""
        caps = [s for s in self.steps if s.get("caption")]
        chapter_start, start, pending = [], 0, False
        for s in self.steps:
            pending = pending or bool(s.get("chapter"))
            if s.get("caption"):
                if pending:
                    start, pending = len(chapter_start), False
                chapter_start.append(start)
        names = {}
        for n, s in enumerate(caps):
            for v in (s.get("focus") or []) + [s["role"]]:
                if v.startswith(COMMENTATOR) and v in self.views and n not in names.setdefault(v, []):
                    names[v].append(n)
        handover, incoming, on_air = {}, {}, None
        for n, s in enumerate(caps):
            pair = [v for v in s.get("focus") or [] if v in names]
            if len(s.get("focus") or ()) == 4 and len(pair) == 2:
                out = on_air if on_air in pair else min(pair, key=lambda v: (names[v][0], pair.index(v)))
                on_air = pair[1] if out == pair[0] else pair[0]
                handover.setdefault(out, []).append(n)
                incoming.setdefault(on_air, []).append(n)
        spans = {}
        for v, ns in names.items():
            group, after = [], False
            for n in ns:
                group.append(n)
                if n in handover.get(v, ()):
                    spans.setdefault(v, []).append((chapter_start[group[0]], n))
                    group, after = [], True
            if group:
                stays = not after or any(n in incoming.get(v, ()) for n in group)
                spans.setdefault(v, []).append((chapter_start[group[0]], len(caps) if stays else group[-1]))

        def on(v, n):
            if _tab_of(v):
                return v in (caps[n].get("focus") or []) + [caps[n]["role"]]
            if v not in spans:
                return True
            return any(a <= n <= b for a, b in spans[v])
        return [[v for v in self.views if on(v, n)] for n in range(len(caps))]

    def _layout_switches(self):
        """[(output time, Layout, from rects, previous Layout, views on the stage)]: the layout
        at the start of each caption and whenever a view's first recording starts, since a
        view takes a place on the stage only from then on."""
        caps = [s for s in self.steps if s.get("caption")]
        shown = [self._marks[s["id"]]["shown"] for s in caps]
        opens = {}
        for r in self.recordings:
            if r.get("file") and r["view"] in self.views:
                opens[r["view"]] = min(r["start"], opens.get(r["view"], r["start"]))
        times = sorted({0.0, *shown, *(self.out_time(a) for a in opens.values() if a > self.t0)})
        switches, layout, present = [], Layout(()), None
        for t in times:
            n = max(0, bisect.bisect_right(shown, t) - 1)
            s = self.src_time(t)
            placed = self._present[n] if caps else list(self.views)
            now = [v for v in placed if v in opens and opens[v] <= s]
            new = focus_layout(caps[n], layout, now) if caps else Layout(())
            if not new.large and now:
                new = Layout((now[0],))
            if switches and new == layout and now == present:
                continue
            if not switches:
                switches.append((0.0, new, self._goal(new, now, None), new, now))
            else:
                t = max(switches[-1][0], t)
                start = self._rects_at(switches, t)[0]
                goal = self._goal(new, now, start)
                # a view coming on fades in where it will stand
                start = {v: r if r[4] > 0 else (*goal[v][:4], 0.0, goal[v][5]) for v, r in start.items()}
                switches.append((t, new, start, layout, now))
            layout, present = new, now
        return switches

    def _goal(self, layout, present, start):
        """The rects a layout rests at; a view off the stage fades out where it stands."""
        goal = target_rects(present, layout, self.sizes)
        for v in self.views:
            if v not in goal:
                r = start[v] if start else (0.0, 0.0, 1.0, 1.0, 0.0, TAB_RAIL)
                goal[v] = (*r[:4], 0.0, r[5])
        return goal

    def _rects_at(self, switches, t):
        k = max(0, bisect.bisect_right([sw[0] for sw in switches], t) - 1)
        at, layout, start, before, present = switches[k]
        goal = self._goal(layout, present, start)
        p = _ease(min(1.0, (t - at) / MOVE_S)) if MOVE_S > 0 else 1.0
        moving = p < 1.0
        single = layout if len(layout.large) == 1 and not layout.quarters else None
        if single is None and moving and len(before.large) == 1 and not before.quarters:
            single = before
        front = single.large[0] if single else None
        return {v: _lerp(start[v], goal[v], p) for v in self.views}, layout, moving, front

    def _ring_windows(self):
        """{view: [(a, b, output)]}: a captioned step rings its role from its action to the end
        of its caption, in output time; any other step from start to acted plus the hold, in
        source time."""
        rings = {v: [] for v in self.views}
        for s in self.steps:
            if s["role"] not in rings:
                continue
            if s.get("caption"):
                rings[s["role"]].append((self._marks[s["id"]]["act"], self._marks[s["id"]]["end"], True))
            else:
                rings[s["role"]].append((s["start"], s["acted"] + self.hold, False))
        return rings

    # texts

    def _make_cues(self):
        rows, params = [], beats(self.steps, self.lead_in, self.hold)
        for s in self.steps:
            if not s.get("caption"):
                continue
            tail = params[s["id"]][4]
            if tail and tail != s["id"]:
                rows[-1] = (rows[-1][0], self._marks[s["id"]]["end"], rows[-1][2], rows[-1][3])
            else:
                rows.append((self._marks[s["id"]]["shown"], self._marks[s["id"]]["end"], label(s["role"]), s["caption"]))
        cues = []
        for n, (a, b, who, text) in enumerate(rows):
            if n + 1 < len(rows):
                b = min(b, rows[n + 1][0])
            if b > a:
                cues.append((a, b, who, text))
        return cues

    def _make_chapters(self):
        rows = [(self._marks[s["id"]]["shown"] if s["id"] in self._marks
                 else self.out_time(s["start"] - self.lead_in), s["chapter"])
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
        speed = self._out_seg(t).speed
        rects, layout, moving, front = self._rects_at(self._switches, t)
        cue = next((c for c in self._cues if c[0] <= t < c[1]), None)
        chapter = None
        for k, (ct, title) in enumerate(self._chapters):
            if ct <= t:
                chapter = (k, ct, title)
        rings = [v for v, ws in self._rings.items() if any(a < (t if out else s) <= b for a, b, out in ws)]
        return {
            "i": i, "t": s, "speed": speed, "ff": f"{speed:g}x" if speed > 1 else "",
            "large": list(layout.large), "quarters": layout.quarters, "moving": moving, "front": front,
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
