"""Record mode of the event simulation (`event_sim.py --record DIR`, #1310).

Every page role records its page as a video into DIR/pages/<role>/, with a visible
pointer and click mark and the console links masked in the page before a frame is
painted. DIR/timeline.json says when each step ran and when each recording started,
both in seconds since the engine started, so the renderer can line them up."""
import contextlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

import e2e_safety
import stream_stub
from roles import selectors

TIMELINE = "timeline.json"
# The record run's own limits, checked before the stack starts: the unit's on Linux, the
# watchdog's memory elsewhere. Memory: about twice the tree's peak of `full` on macOS.
RECORD_MEMORY = 6 * 1024**3
RECORD_TASKS = 4096
RECORD_CPU_PERCENT = 600
LIMITS = e2e_safety.Limits(RECORD_MEMORY, RECORD_TASKS, RECORD_CPU_PERCENT)
PLATFORMS = ("linux", "darwin")
WATCH_INTERVAL_S = 5.0
WATCH_GRACE_S = 60.0     # after the stop request, how long the run may take before its tree is killed
CLIP_SECONDS = 9
CLIP_SIZE = (1920, 1080)
CLIP_FPS = 30
CLIP_TIMEOUT_S = 120
CLIPS = {"intro": "Intro", "trailer": "Trailer", "outro": "Outro"}   # file stem -> title
CARD_SIZE = (640, 360)   # the stub's largest picture; the card scales its images to the pull's size
CLOCK_SIDE = 128
# The filters the card look and the clips use; all in a stock build, drawtext is not.
FILTERS = ("gradients", "color", "sine", "anullsrc", "overlay", "scale", "format", "rotate", "fade", "drawbox",
           "null")
PAGES = "pages"
STEP_KEYS = ("caption", "chapter", "focus", "card")    # the film's step keys, when the scenario has them
FILM_KEYS = ("intro", "outro")

# Drawn only in record mode, in the top frame, above everything and never hit by a click.
POINTER_SCRIPT = """(() => {
  if (window.top !== window) return;
  const css = `#__simPointer{position:fixed;left:0;top:0;width:22px;height:22px;margin:-3px 0 0 -3px;
    z-index:2147483647;pointer-events:none;border:3px solid #fff;border-radius:50% 50% 50% 0;
    background:rgba(255,64,64,.85);box-shadow:0 0 6px rgba(0,0,0,.7);transform:translate(-100px,-100px)}
    .__simClick{position:fixed;z-index:2147483646;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;
    border:4px solid rgba(255,64,64,.9);border-radius:50%;animation:__simClick .6s ease-out forwards}
    @keyframes __simClick{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}`;
  const add = () => {
    if (document.getElementById('__simPointer')) return;
    const style = document.createElement('style');
    style.textContent = css;
    const dot = document.createElement('div');
    dot.id = '__simPointer';
    document.documentElement.append(style, dot);
    addEventListener('mousemove', e => { dot.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, true);
    addEventListener('mousedown', e => {
      const ring = document.createElement('div');
      ring.className = '__simClick';
      ring.style.left = e.clientX + 'px';
      ring.style.top = e.clientY + 'px';
      document.documentElement.append(ring);
      setTimeout(() => ring.remove(), 700);
    }, true);
  };
  if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add);
})()"""


def mask_script(masked=selectors.LINKS):
    """An init script that paints every match of `masked` (and its children) black. It
    changes paint only, so a role still finds and reads the element, and it is adopted
    before the first paint."""
    targets = ", ".join(f"{s}, {s} *" for s in masked)
    rule = (f"{targets} {{ color: #000 !important; -webkit-text-fill-color: #000 !important; "
            "background: #000 !important; text-shadow: none !important; caret-color: transparent !important; "
            "border-color: #000 !important; }")
    return ("(() => { const sheet = new CSSStyleSheet(); sheet.replaceSync(" + json.dumps(rule) + ");"
            " document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]; })()")


def rel_file(path, base, pathmod=os.path):
    """`path` relative to `base` with forward slashes; on another drive (Windows), where no
    relative path exists, the absolute path with forward slashes. None stays None."""
    if not path:
        return None
    try:
        return pathmod.relpath(path, base).replace(pathmod.sep, "/")
    except ValueError:
        return path.replace(pathmod.sep, "/")


def safe_name(name):
    """A role name as a directory name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


class Recorder:
    """Where a record run puts its recordings, and what it notes about them."""

    def __init__(self, directory, clock=time.monotonic, render=None):
        self.dir = os.path.abspath(directory)
        self.clock = clock
        self.render = render or render_pngs
        self.recordings = []

    def channel_card(self, stub_dir, channel, browser=None):
        """Render the card of a channel created or changed during the run (`channel` has
        `channel`, `stint` and `name`) in the run's own browser."""
        self.render(card_jobs(stub_dir, [channel]), browser=browser)

    def context_options(self, role, viewport):
        """The browser context options that record the role's page at its viewport."""
        return {"record_video_dir": os.path.join(self.dir, PAGES, safe_name(role)),
                "record_video_size": dict(viewport)}

    def init_scripts(self):
        return [mask_script(), POINTER_SCRIPT]

    def page_opened(self, role, page, viewport):
        """Note the recording of `role`'s new page; it starts now."""
        video = getattr(page, "video", None)
        path = video.path() if video is not None else None
        self._add(role, "page", path, viewport)

    def page_closed(self, role):
        """`role`'s page closed: its running recording ends now."""
        for r in reversed(self.recordings):
            if r["view"] == role and r["kind"] == "page" and r.get("end") is None:
                r["end"] = self.clock()
                return

    def program_started(self, path, size=None, at=None):
        """Note the program recording (OBS's own), started at `at` (default now)."""
        self._add("program", "program", path, size, at)

    def program_file(self, path):
        """The program recording's file, known once OBS stopped it."""
        for r in self.recordings:
            if r["kind"] == "program":
                r["file"] = rel_file(path, self.dir)

    def _add(self, view, kind, path, size, at=None):
        rel = rel_file(path, self.dir)
        self.recordings.append({"view": view, "kind": kind, "file": rel, "size": size,
                                "at": self.clock() if at is None else at})

    def timeline(self, scenario, steps, start):
        """The timeline of a run whose engine started at `start` (this recorder's clock).
        `steps` are the report's steps; their scenario steps add caption, chapter, focus and card,
        the scenario its intro and outro.
        A step's action ran from `start` to `acted`; from `acted` to `end` it waited for its expects."""
        by_id = {s.id: s for s in getattr(scenario, "steps", [])}
        out = []
        for s in steps:
            if s.get("started") is None:
                continue
            acted = s["started"] if s.get("acted") is None else s["acted"]
            row = {"id": s["id"], "role": s["role"], "action": s["do"], "status": s["status"],
                   "start": round(s["started"], 3), "acted": round(acted, 3),
                   "end": round(s["started"] + (s.get("duration") or 0.0), 3)}
            src = by_id.get(s["id"])
            for key in STEP_KEYS:
                value = getattr(src, key, None)
                if value is not None:
                    row[key] = value
            out.append(row)
        recordings = [{"view": r["view"], "kind": r["kind"], "file": r["file"], "size": r["size"],
                       "start": round(r["at"] - start, 3),
                       **({"end": round(r["end"] - start, 3)} if r.get("end") is not None else {})}
                      for r in self.recordings]
        film = {key: getattr(scenario, key) for key in FILM_KEYS if getattr(scenario, key, None) is not None}
        return {"scenario": getattr(scenario, "name", ""), **film, "steps": out, "recordings": recordings}

    def write(self, timeline):
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, TIMELINE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(timeline, fh, indent=1)
        return path


def _css(color):
    return "#" + color[2:]


def title_html(lines, size):
    """A transparent page of `size` with `lines` [(text, colour, share of the height)]
    centred above each other, for a card or a clip title."""
    w, h = size
    rows = "".join(f'<div style="color:{_css(color)};font-size:{max(10, int(h * share))}px;'
                   f'font-weight:{600 if n == 0 else 400};margin:{int(h * 0.02)}px 0">{html.escape(text)}</div>'
                   for n, (text, color, share) in enumerate(lines) if text)
    return (f'<html><body style="margin:0;width:{w}px;height:{h}px;background:transparent;display:flex;'
            "flex-direction:column;align-items:center;justify-content:center;"
            f'font-family:Helvetica,Arial,sans-serif;padding-bottom:{h // 8}px;box-sizing:border-box">{rows}'
            "</body></html>")


def dial_html(side=CLOCK_SIDE):
    ticks = "".join(f'<div style="position:absolute;left:{side // 2 - 2}px;top:4px;width:4px;height:{side // 10}px;'
                    f'background:{_css(stream_stub.CARD_MUTED)};transform-origin:2px {side // 2 - 4}px;'
                    f'transform:rotate({n * 30}deg)"></div>' for n in range(12))
    return (f'<html><body style="margin:0;background:transparent"><div style="position:relative;width:{side}px;'
            f'height:{side}px;border-radius:50%;background:{_css(stream_stub.CARD_BG)}">{ticks}</div></body></html>')


def hand_html(side=CLOCK_SIDE):
    return (f'<html><body style="margin:0;background:transparent"><div style="position:relative;width:{side}px;'
            f'height:{side}px"><div style="position:absolute;left:{side // 2 - 2}px;top:{side // 8}px;width:4px;'
            f'height:{side // 2 - side // 8}px;border-radius:2px;background:{_css(stream_stub.CARD_TEXT)}"></div>'
            "</div></body></html>")


def _render_with(browser, jobs):
    for page_html, path, (w, h) in jobs:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        page = browser.new_page(viewport={"width": w, "height": h})
        try:
            page.set_content(page_html)
            tmp = path + ".part.png"
            page.screenshot(path=tmp, omit_background=True)
            os.replace(tmp, path)      # a pull never reads a half-written image
        finally:
            page.close()


def render_pngs(jobs, browser=None):
    """Render each (html, path, (width, height)) as a transparent PNG with Playwright's
    Chromium, the browser a record run needs anyway: stock ffmpeg builds may lack drawtext.
    `browser` is the run's open browser; without one a session of its own renders them."""
    if browser is not None:
        _render_with(browser, jobs)
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        own = pw.chromium.launch()
        try:
            _render_with(own, jobs)
        finally:
            own.close()


def card_jobs(stub_dir, channels):
    """The render jobs of the card look: each channel's title (stint and commentator),
    the clock's dial and hand. `channels` have `channel`, `stint` and `name`."""
    size = (CARD_SIZE[0], CARD_SIZE[1])
    jobs = []
    for c in channels:
        name = stream_stub.channel_name(c.channel)
        if name is None:
            continue
        paths = stream_stub.card_paths(stub_dir, name)
        lines = [(f"Stint {c.stint}" if c.stint else "", stream_stub.CARD_TEXT, 0.2),
                 (c.name or "", stream_stub.CARD_MUTED, 0.1)]
        jobs.append((title_html(lines, size), paths.title, size))
    if jobs:
        paths = stream_stub.card_paths(stub_dir, "x")
        jobs += [(dial_html(), paths.dial, (CLOCK_SIDE, CLOCK_SIDE)), (hand_html(), paths.hand, (CLOCK_SIDE, CLOCK_SIDE))]
    return jobs


def clip_jobs(directory, subtitle):
    """({stem: title PNG path}, render jobs) of the intro, trailer and outro titles."""
    images = {stem: os.path.join(directory, stem + ".png") for stem in CLIPS}
    jobs = [(title_html([(title, stream_stub.CARD_TEXT, 1 / 6), (subtitle, stream_stub.CARD_MUTED, 1 / 14)],
                        CLIP_SIZE), images[stem], CLIP_SIZE) for stem, title in CLIPS.items()]
    return images, jobs


GRAPHICS_TOKEN = "__RACECAST_GRAPHICS__/"
# Light plates behind the demo HUD's team logos (profiles/demo/overlay/layout-hud.json), so a
# dark brand stays legible on the dark lower panel.
TEAM_LOGOS = ((324, 1013, 54, 50), (768, 1013, 54, 50), (1212, 1013, 54, 50))
PLATE_MARGIN = 4
PLATE_COLOUR = "#eef0f2"
FLAG_COLOURS = {"Green": "#1f8a3b", "Yellow": "#f2c014", "Red": "#c62121", "Safety Car": "#f2c014",
                "Virtual Safety Car": "#f2c014"}    # the HUD's flag colours (src/obs/hud.html)


def collection_graphics(collection):
    """The graphic labels a collection's image sources read (`__RACECAST_GRAPHICS__/<Label>.png`)."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and value.startswith(GRAPHICS_TOKEN) and value.endswith(".png"):
            found.add(value[len(GRAPHICS_TOKEN):-len(".png")])
    walk(collection)
    return sorted(found)


def graphic_html(label, subtitle, size=CLIP_SIZE):
    """A demo stand-in for the league graphic `label`. Overlay, the flags, the grid rows and the
    interview frame lie over a live picture and stay transparent outside their own frame or band; every other
    graphic is a full page in the card colours with its name and the league's."""
    w, h = size
    body = f'<html><body style="margin:0;width:{w}px;height:{h}px;background:transparent;position:relative">'
    box = "position:absolute;box-sizing:border-box;"
    if label == "Overlay":
        return body + "".join(f'<div style="{box}background:{_css(stream_stub.CARD_BG)};left:{x}px;top:{y}px;'
                              f'width:{w}px;height:{h}px;border-radius:10px"></div>'
                              for x, y, w, h in stream_stub.OVERLAY_PANELS) + \
            "".join(f'<div style="{box}background:{PLATE_COLOUR};left:{x - PLATE_MARGIN}px;top:{y - PLATE_MARGIN}px;'
                    f'width:{w + 2 * PLATE_MARGIN}px;height:{h + 2 * PLATE_MARGIN}px;border-radius:6px"></div>'
                    for x, y, w, h in TEAM_LOGOS) + "</body></html>"
    if label.startswith("Flag "):
        kind = label[len("Flag "):]
        paint = FLAG_COLOURS.get(kind, "repeating-conic-gradient(#111 0 25%,#fff 0 50%) 0 0/28px 28px")
        edges = ("left:0;top:0;width:100%;height:14px", "left:0;bottom:0;width:100%;height:14px",
                 "left:0;top:0;width:14px;height:100%", "right:0;top:0;width:14px;height:100%")
        return body + "".join(f'<div style="{box}{e};background:{paint}"></div>' for e in edges) + "</body></html>"
    if label == "Post Race Interviews":
        return (body + f'<div style="{box}left:0;top:0;width:100%;height:110px;background:{_css(stream_stub.CARD_BG)};'
                f"color:{_css(stream_stub.CARD_TEXT)};font:600 48px Helvetica,Arial,sans-serif;display:flex;"
                f'align-items:center;justify-content:center">{html.escape(label)}</div></body></html>')
    if label.startswith("Grid Row "):
        n = int(label.rsplit(" ", 1)[1]) if label.rsplit(" ", 1)[1].isdigit() else 1
        return (body + f'<div style="{box}left:260px;top:{150 + (n - 1) * 92}px;width:1400px;height:80px;'
                f"background:{_css(stream_stub.CARD_PANEL)};color:{_css(stream_stub.CARD_TEXT)};"
                "font:600 36px Helvetica,Arial,sans-serif;display:flex;align-items:center;padding-left:32px"
                f'">Row {n}</div></body></html>')
    title = title_html([(label, stream_stub.CARD_TEXT, 1 / 10), (subtitle, stream_stub.CARD_MUTED, 1 / 22)], size)
    return title.replace("background:transparent", f"background:linear-gradient(135deg,"
                         f"{_css(stream_stub.CARD_BG)},{_css(stream_stub.CARD_PANEL)})", 1)


def graphic_jobs(directory, labels, subtitle):
    """Render jobs of the demo graphics `labels` as `directory`/<Label>.png."""
    return [(graphic_html(label, subtitle), os.path.join(directory, label + ".png"), CLIP_SIZE) for label in labels]


def clip_argv(ffmpeg, out, image, seconds=CLIP_SECONDS, size=CLIP_SIZE):
    """ffmpeg writing a title clip to `out` (mp4, silent audio track): the card look's
    colours under the rendered title `image`, a fade in and out."""
    w, h = size
    graph = f"[0:v][2:v]overlay=0:0,fade=t=in:st=0:d=1,fade=t=out:st={seconds - 1}:d=1[v]"
    return [*ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-filter_threads", "1",
            "-filter_complex_threads", "1",
            "-f", "lavfi", "-i", f"gradients=size={w}x{h}:rate={CLIP_FPS}:c0={stream_stub.CARD_BG}"
                                 f":c1={stream_stub.CARD_PANEL}:n=2:speed=0.004:duration={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={seconds}",
            "-loop", "1", "-framerate", str(CLIP_FPS), "-t", str(seconds), "-i", image,
            "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
            "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p", "-threads", "2",
            "-c:a", "aac", "-b:a", "96k", "-t", str(seconds), "-movflags", "+faststart", out]


def make_clips(media_dir, images, ffmpeg=("ffmpeg",), run=subprocess.run):
    """Write intro.mp4, trailer.mp4 and outro.mp4 into `media_dir`, where the localized
    collection's media sources read them, each over its title PNG in `images` ({stem:
    path}). Returns their paths; raises RuntimeError."""
    os.makedirs(media_dir, exist_ok=True)
    paths = []
    for stem in CLIPS:
        out = os.path.join(media_dir, stem + ".mp4")
        try:
            done = run(clip_argv(list(ffmpeg), out, images[stem]), capture_output=True, text=True,
                       errors="replace", timeout=CLIP_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"{stem} clip: {exc}") from None
        if done.returncode != 0:
            raise RuntimeError(f"{stem} clip: ffmpeg exited {done.returncode}: {done.stderr[-300:]}")
        paths.append(out)
    return paths


def _total_memory_darwin(run=subprocess.run):
    done = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, errors="replace", timeout=10)
    return int(done.stdout.strip())


def _has_playwright():
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def tools_problem(run=subprocess.run, playwright=_has_playwright):
    """What the record run's pictures lack, or "": the ffmpeg filters in FILTERS and
    Playwright, which records the pages and renders the titles."""
    try:
        done = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace",
                   timeout=30)
        names = {line.split()[1] for line in done.stdout.splitlines() if len(line.split()) > 2}
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ffmpeg did not answer ({exc})"
    missing = [f for f in FILTERS if f not in names]
    if missing:
        return f"ffmpeg lacks the filters {', '.join(missing)}"
    return "" if playwright() else "Playwright is not installed (see tools/CLAUDE.md, e2e visual run)"


def admission(limits=LIMITS, platform=None, host=e2e_safety.host_memory, total=_total_memory_darwin,
              tools=tools_problem):
    """Why this machine cannot take a record run, or "". Checked before the stack starts."""
    platform = platform or sys.platform
    if platform not in PLATFORMS:
        return f"record mode runs on Linux or macOS, not {platform}"
    problem = tools()
    if problem:
        return problem
    need = limits.memory + e2e_safety.MEMORY_MARGIN
    if platform == "linux":
        return e2e_safety.memory_strain(host(), limits.memory)
    try:
        have = total()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"the machine's memory is unknown ({exc}), and record mode needs {need // 1024**2} MiB"
    return "" if have >= need else f"the machine has {have // 1024**2} MiB, record mode needs {need // 1024**2} MiB"


def in_unit(limits=LIMITS):
    """True inside a verified Linux unit with `limits`: the kernel enforces them, no watchdog."""
    if sys.platform != "linux":
        return False
    try:
        return e2e_safety.limited(e2e_safety.current_group(), limits)
    except (RuntimeError, OSError):
        return False


def ps_rows(run=subprocess.run):
    """Every process as (pid, ppid, rss in bytes, start): `ps -A` on Linux and macOS."""
    done = run(["ps", "-A", "-o", "pid=,ppid=,rss=,lstart="], capture_output=True, text=True,
               errors="replace", timeout=20)
    rows = []
    for line in done.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]) * 1024, parts[3].strip()))
    return rows


def descendants(rows, root, known):
    """The pids below `root`, walking ppid whatever their process group. `known` maps each
    pid seen below it to its start time and is updated, so a child reparented after its
    parent died still counts, and a reused pid does not."""
    children = {}
    for pid, ppid, _rss, start in rows:
        children.setdefault(ppid, []).append((pid, start))
    found, todo = set(), [root]
    while todo:
        for pid, start in children.get(todo.pop(), ()):
            if pid not in found:
                found.add(pid)
                known[pid] = start
                todo.append(pid)
    for pid, _ppid, _rss, start in rows:
        if pid not in found and known.get(pid) == start:
            found.add(pid)
            todo = [pid]
            while todo:
                for child, cstart in children.get(todo.pop(), ()):
                    if child not in found:
                        found.add(child)
                        known[child] = cstart
                        todo.append(child)
    return found


@contextlib.contextmanager
def deferred_signals(names=("SIGINT", "SIGTERM", "SIGHUP")):
    """Hold SIGINT, SIGTERM and SIGHUP while the block runs (a teardown, quitting OBS and
    restoring the user's configuration must not be cut short), then deliver them to the
    handlers before. Yields the held signal numbers; an ignored signal stays ignored."""
    held, old = [], {}
    for name in names:
        num = getattr(signal, name, None)
        if num is not None and signal.getsignal(num) is not signal.SIG_IGN:
            old[num] = signal.signal(num, lambda n, f: held.append(n))
    try:
        yield held
    finally:
        for num, handler in old.items():
            signal.signal(num, handler)
        for num in held:
            signal.raise_signal(num)


class Watchdog:
    """Stops a record run whose process tree (the run and all it started) passes `limit`
    bytes: first it asks the run to stop (`interrupt`, a SIGINT to the run by default, so
    its cleanup still quits OBS and restores what it changed), and when the tree is still
    over the limit WATCH_GRACE_S later it kills the descendants. For a run outside a
    Linux unit, where no kernel enforces the limit."""

    def __init__(self, limit, root=None, ps=ps_rows, interrupt=None, kill=os.kill, clock=time.monotonic,
                 interval=WATCH_INTERVAL_S, grace=WATCH_GRACE_S):
        self.limit, self.root = limit, root or os.getpid()
        self.ps, self.kill, self.clock = ps, kill, clock
        self.interrupt = interrupt or (lambda: os.kill(self.root, signal.SIGINT))
        self.interval, self.grace = interval, grace
        self.known, self.peak, self.reason, self.tripped, self.killed = {}, 0, "", None, []
        self._stop, self._thread = threading.Event(), None

    def check(self):
        """One sample; returns the tree's resident bytes."""
        rows = self.ps()
        tree = descendants(rows, self.root, self.known) | {self.root}
        rss = sum(r for pid, _ppid, r, _start in rows if pid in tree)
        self.peak = max(self.peak, rss)
        if rss <= self.limit:
            return rss
        if self.tripped is None:
            self.tripped = self.clock()
            self.reason = (f"the record run's process tree used {rss // 1024**2} MiB, over its limit of "
                           f"{self.limit // 1024**2} MiB: the watchdog stopped the run")
            self.interrupt()
        elif self.clock() - self.tripped >= self.grace:
            for pid in sorted(tree - {self.root}):
                try:
                    self.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                    self.killed.append(pid)
                except OSError:
                    pass  # gone meanwhile
        return rss

    def disarm(self):
        """From now on the run is only killed, never interrupted: its cleanup must finish."""
        self.interrupt = lambda: None

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.check()
            except (OSError, subprocess.SubprocessError, ValueError):
                pass  # one missed sample; the next one decides

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="record-watchdog")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.interval + 5)
