"""Render the event walkthrough film (#1312): transcode each recording once, capture the
stage page frame by frame in parallel workers, encode their segments and add the music.

Each worker drives its own Chromium through Playwright and pipes JPEG screenshots into
its own ffmpeg. Seeking VP8 with keyframes far apart dominates the frame time, so every
recording is first transcoded to all-keyframe VP8 (gate G3). No drawtext: every text is
drawn by the stage page. A frame whose video did not load or seek fails its segment,
which stays unfinished and is rendered again on the next run."""
import concurrent.futures
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

import film_plan as fp

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
FONTS_DIR = os.path.join(REPO_ROOT, "src", "docs", "slides", "assets", "fonts")
STAGE = "stage.html"
SEGMENT_FRAMES = 1800
PROGRAM_WIDTH = 1600     # the widest large slot; the 1080p program needs no more
JPEG_QUALITY = 92
MUSIC_VOLUME = 0.25
MUSIC_FADE_S = 3.0
RANGE_CHUNK = 8 * 1024**2    # Chromium asks for the next range, so a paused video holds no connection
TRANSCODE_BITRATE = 4_000_000
TRANSCODE_OVERSHOOT = 2.5    # all-keyframe VP8 at realtime speed writes about twice its target bitrate
TRANSCODE_TIMEOUT_S = 4 * 3600
FILM = "event-walkthrough.mp4"


def transcode_argv(ffmpeg, src, out, program=False):
    """All-keyframe VP8 without audio, so the stage seeks any frame directly."""
    scale = ["-vf", f"scale='min({PROGRAM_WIDTH},iw)':-2"] if program else []
    return [*ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-an", *scale,
            "-c:v", "libvpx", "-g", "1", "-b:v", "4M", "-deadline", "realtime", "-cpu-used", "8",
            "-threads", "2", out]


def segment_argv(ffmpeg, fps, out):
    """ffmpeg encoding the JPEG frames piped to its stdin."""
    return [*ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "image2pipe",
            "-c:v", "mjpeg", "-framerate", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps), "-threads", "2", out]


def final_argv(ffmpeg, concat_list, out, duration, music=None):
    """The segments joined without re-encoding, with the music bed looped, quiet and faded."""
    argv = [*ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-stats", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list]
    if music:
        fade_out = max(0.0, duration - MUSIC_FADE_S)
        argv += ["-stream_loop", "-1", "-i", music, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-af", f"volume={MUSIC_VOLUME},afade=t=in:st=0:d=2,afade=t=out:st={fade_out:.3f}:d={MUSIC_FADE_S}",
                 "-c:a", "aac", "-b:a", "192k"]
    else:
        argv += ["-map", "0:v:0", "-c:v", "copy"]
    return argv + ["-t", f"{duration:.3f}", "-movflags", "+faststart", out]


def byte_range(header, size, chunk=None):
    """(first, last) of a single `bytes=` range, at most `chunk` bytes long, None without a
    usable header, False when it cannot be satisfied."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].strip().partition("-")
    try:
        if not first:
            n = int(last)
            if n <= 0:
                return False
            a, b = max(0, size - n), size - 1
        else:
            a = int(first)
            b = min(size - 1, int(last)) if last else size - 1
    except ValueError:
        return None
    return (a, min(b, a + (chunk or RANGE_CHUNK) - 1)) if a <= b else False


def segments(a, b, size=SEGMENT_FRAMES):
    """Frames [a, b) cut at multiples of `size`, so a partial render reuses full segments."""
    out, at = [], a
    while at < b:
        nxt = min(b, (at // size + 1) * size)
        out.append((at, nxt))
        at = nxt
    return out


def pending(segs):
    """The segments without a finished file; an unfinished one only ever has its .part."""
    return [seg for seg in segs if not os.path.isfile(seg[2])]


def concat_text(paths):
    """ffmpeg's concat list; absolute paths, since entries resolve against the list's dir."""
    return "".join("file '" + os.path.abspath(p).replace(os.sep, "/").replace("'", "'\\''") + "'\n" for p in paths)


def slices(segs, workers):
    """Contiguous runs of segments, one per worker."""
    n = max(1, min(workers, len(segs)))
    base, extra = divmod(len(segs), n)
    out, at = [], 0
    for k in range(n):
        take = base + (1 if k < extra else 0)
        out.append(segs[at:at + take])
        at += take
    return out


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def served_files(mounts):
    """{URL path: file} for every file under the mounts ({prefix: dir})."""
    out = {}
    for prefix, root in mounts.items():
        root = os.path.abspath(root)
        for base, _dirs, files in os.walk(root):
            for name in files:
                full = os.path.join(base, name)
                out[prefix + os.path.relpath(full, root).replace(os.sep, "/")] = full
    return out


def resolve(mounts, path):
    """The file a URL path names under one of `mounts`, None for anything else. The answer
    comes from the files that exist there, never from the request's own path."""
    clean = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
    return served_files(mounts).get(clean)


def _handler(mounts):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            return resolve(mounts, path) or ""     # open("") fails, so the request gets a 404

        def end_headers(self):
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            path = self.translate_path(self.path)
            span = byte_range(self.headers.get("Range"), os.path.getsize(path)) if os.path.isfile(path) else None
            if span is None:
                return super().do_GET()
            size = os.path.getsize(path)
            if span is False:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            a, b = span
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            with open(path, "rb") as fh:
                fh.seek(a)
                left = b - a + 1
                try:
                    while left > 0:
                        chunk = fh.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass    # the browser cancels a range it no longer needs
            return None

        def log_message(self, *args):
            pass    # one line per range request would drown the progress

    return Handler


def serve(media_dir):
    """A loopback server for the stage page, its fonts and the transcoded recordings."""
    mounts = {"/stage/": HERE, "/fonts/": FONTS_DIR, "/media/": media_dir}
    httpd = _Server(("127.0.0.1", 0), _handler(mounts))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def stop(httpd):
    httpd.shutdown()
    httpd.server_close()


def probe_duration(ffprobe, path, run=subprocess.run):
    try:
        done = run([*ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                   capture_output=True, text=True, errors="replace", timeout=60)
        return float(done.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def media_name(n, src, program=False):
    """The transcode's file name: the recording index and a hash of the source's path, size
    and time and of the transcode parameters, so a changed input transcodes again."""
    st = os.stat(src)
    key = [os.path.realpath(src), st.st_size, st.st_mtime_ns, transcode_argv(["ffmpeg"], "IN", "OUT", program)]
    return f"{n}-{hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]}.webm"


def transcode_bytes(plan, jobs):
    """An estimate of the disk the transcodes of `jobs` [(index, recording)] take: each
    recording runs from its start to the end of the run."""
    seconds = sum(max(0.0, plan.t1 - r["start"]) for _, r in jobs)
    return int(seconds * TRANSCODE_BITRATE / 8 * TRANSCODE_OVERSHOOT)


def check_disk(directory, need, free=lambda d: shutil.disk_usage(d).free):
    have = free(directory)
    if have < need:
        raise RuntimeError(f"the transcodes need about {need / 1e9:.1f} GB in {directory}, "
                           f"only {have / 1e9:.1f} GB are free")


def transcode_workers(workers, cpus=None):
    """How many transcodes run at once: at most half the cores, each ffmpeg uses two threads."""
    if cpus is None:
        cpus = os.cpu_count() or 2
    return max(1, min(workers, cpus // 2))


def transcode_all(plan, rec_dir, media_dir, ffmpeg, ffprobe, workers, run=subprocess.run, log=print):
    """Transcode every staged recording into media_dir once. Returns ({recording index: url
    path}, {file: duration}); a recording without a source file is left out. Raises
    RuntimeError."""
    os.makedirs(media_dir, exist_ok=True)
    jobs = []
    for n, r in enumerate(plan.recordings):
        if r["view"] not in plan.views or not r.get("file"):
            continue
        src = r["file"] if os.path.isabs(r["file"]) else os.path.join(rec_dir, *r["file"].split("/"))
        if not os.path.isfile(src):
            log(f"missing recording {r['file']}: {r['view']} shows an earlier one or its placeholder")
            continue
        program = r.get("kind") == "program"
        jobs.append((n, r, src, os.path.join(media_dir, media_name(n, src, program)), program))
    todo = [(n, r) for n, r, _, out, _ in jobs if not os.path.isfile(out)]
    check_disk(media_dir, transcode_bytes(plan, todo))

    def one(job):
        n, r, src, out, program = job
        if not os.path.isfile(out):
            part = out[:-5] + ".part.webm"
            log(f"transcode {r['file']}")
            done = run(transcode_argv(ffmpeg, src, part, program=program),
                       capture_output=True, text=True, errors="replace", timeout=TRANSCODE_TIMEOUT_S)
            if done.returncode != 0:
                raise RuntimeError(f"transcode {r['file']}: ffmpeg exited {done.returncode}: {done.stderr[-300:]}")
            os.replace(part, out)
        return n, r["file"], os.path.basename(out), probe_duration(ffprobe, out, run=run)

    urls, durations = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=transcode_workers(workers)) as pool:
        for n, file, name, dur in pool.map(one, jobs):
            urls[n] = "/media/" + name
            if dur is not None:
                durations[file] = dur
    return urls, durations


def plan_key(timeline, options, durations):
    """A fingerprint of everything a segment depends on, its encoding included, so a
    changed stage renders anew and segments of different encodes never get joined."""
    encode = [segment_argv(["ffmpeg"], fp.FPS, "OUT"), JPEG_QUALITY]
    h = hashlib.sha256(json.dumps([timeline, options, durations, encode], sort_keys=True).encode())
    for name in (STAGE, "film_plan.py"):
        with open(os.path.join(HERE, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


def parent_alive(parent, getppid=os.getppid):
    return getppid() == parent


def render_segment(page, plan, a, b, path, ffmpeg, popen=subprocess.Popen, alive=lambda: True, worker=0):
    """Encode frames [a, b) into `path`. A frame the stage could not show fails the segment,
    which then keeps only its .part file. Raises RuntimeError."""
    part = path[:-4] + ".part.mp4"
    proc = popen(segment_argv(ffmpeg, plan.fps, part), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    began = time.monotonic()
    try:
        for i in range(a, b):
            if not alive():
                raise RuntimeError("the render's main process is gone")
            state = plan.frame(i)
            done = page.evaluate("s => setFrame(s)", state)
            if not done.get("ok"):
                raise RuntimeError(f"frame {i} at {state['t']:.2f} s of the run: {'; '.join(done.get('problems') or [])}")
            proc.stdin.write(page.screenshot(type="jpeg", quality=JPEG_QUALITY))
            if (i - a) % 300 == 299:
                rate = (i - a + 1) / (time.monotonic() - began)
                print(f"[worker {worker}] frame {i + 1}/{b} ({rate:.1f} fps)", flush=True)
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"segment {a}-{b}: ffmpeg exited {code}")
    os.replace(part, path)


def capture(job):
    """A worker: render its segments of the film into mp4 files."""
    from playwright.sync_api import sync_playwright
    plan = fp.Plan(job["timeline"], durations=job["durations"], available={int(k) for k in job["urls"]},
                   **job["options"])
    w, h = fp.CANVAS
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--autoplay-policy=no-user-gesture-required"])
        try:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.goto(job["url"] + "/stage/" + STAGE)
            urls = {int(k): job["url"] + v for k, v in job["urls"].items()}
            page.evaluate("cfg => setup(cfg)", plan.stage_config(urls))
            for a, b, path in job["segments"]:
                render_segment(page, plan, a, b, path, job["ffmpeg"], alive=lambda: parent_alive(job["parent"]),
                               worker=job["worker"])
        finally:
            browser.close()
    return 0


def worker_main(job_path):
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    try:
        return capture(job)
    except RuntimeError as exc:
        print(f"[worker {job['worker']}] {exc}", file=sys.stderr, flush=True)
        return 1


def render(plan, timeline, rec_dir, out_dir, frames, workers=4, music=None, options=None,
           ffmpeg=("ffmpeg",), ffprobe=("ffprobe",), script=None, log=print):
    """Render frames [a, b) of the film into out_dir; the whole film is FILM, a part is
    named after its film seconds. Returns an exit code."""
    options = options or {}
    out_dir = os.path.abspath(out_dir)
    work = os.path.join(out_dir, "work")
    media = os.path.join(work, "media")
    try:
        urls, durations = transcode_all(plan, rec_dir, media, list(ffmpeg), list(ffprobe), workers, log=log)
    except RuntimeError as exc:
        log(str(exc))
        return 1
    plan = fp.Plan(timeline, durations=durations, available=set(urls), **options)
    seg_dir = os.path.join(work, "segments", plan_key(timeline, options, durations))
    os.makedirs(seg_dir, exist_ok=True)
    a, b = frames
    segs = [(x, y, os.path.join(seg_dir, f"seg-{x:07d}-{y:07d}.mp4")) for x, y in segments(a, b)]
    todo = pending(segs)
    codes = []
    if todo:
        httpd, url = serve(media)
        procs = []
        try:
            for k, part in enumerate(slices(todo, workers)):
                job = {"worker": k + 1, "parent": os.getpid(), "timeline": timeline, "options": options,
                       "durations": durations, "url": url, "urls": {str(n): u for n, u in urls.items()},
                       "segments": part, "ffmpeg": list(ffmpeg)}
                path = os.path.join(work, f"worker-{k + 1}.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(job, fh)
                procs.append(subprocess.Popen([sys.executable, script, "--worker", path]))
            codes = [p.wait() for p in procs]
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
            raise
        finally:
            stop(httpd)
    if any(codes):
        log(f"a capture worker failed (exit codes {codes}); run again to resume from the finished segments")
        return 1
    concat = os.path.join(work, "segments.txt")
    with open(concat, "w", encoding="utf-8") as fh:
        fh.write(concat_text([path for _, _, path in segs]))
    whole = (a, b) == (0, plan.frames)
    name = FILM if whole else f"event-walkthrough-{int(a / plan.fps)}s-{int(b / plan.fps)}s.mp4"
    out = os.path.join(out_dir, name)
    done = subprocess.run(final_argv(list(ffmpeg), concat, out, (b - a) / plan.fps, music=music),
                          capture_output=True, text=True, errors="replace")
    if done.returncode != 0:
        log(f"joining the segments failed: {done.stderr[-500:]}")
        return 1
    log(f"wrote {out}")
    return 0
