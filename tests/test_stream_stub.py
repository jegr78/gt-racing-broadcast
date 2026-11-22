#!/usr/bin/env python3
"""The event simulation's stream stub (#1231). Run: python3 tests/test_stream_stub.py"""
import glob
import importlib.util
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import loopstream  # noqa: E402
import preflight  # noqa: E402
import simulation  # noqa: E402
import stream_stub  # noqa: E402

for _key in [k for k in os.environ if k.startswith("RACECAST_")]:
    del os.environ[_key]
os.environ["RACECAST_SIMULATION"] = "1"
os.environ.setdefault("RACECAST_MANUAL_FEED_ARM", "0")

_spec = importlib.util.spec_from_file_location("stubfeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)
LOG = logging.getLogger("test_stream_stub")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False
WAIT = 60
FAKE_FFMPEG = os.path.join(HERE, "fixtures", "fake_ffmpeg.py")


class Skip(Exception):
    pass


def _stub(**kw):
    kw.setdefault("ffmpeg", [sys.executable, FAKE_FFMPEG])
    return stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-"), **kw).install()


def _on_path(stub):
    """Put the stand-ins first on this process's PATH, as the relay does."""
    old = os.environ.get("PATH", "")
    os.environ.update(simulation.with_stream_bin(dict(os.environ, **stub.env())))

    def restore():
        os.environ["PATH"] = old
    return restore


class _Reader:
    """Drains a pipe in a thread; waits are bounded by WAIT."""

    def __init__(self, stream):
        self.stream, self.data = stream, bytearray()
        self.got = threading.Condition()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for chunk in iter(lambda: self.stream.read1(65536), b""):
            with self.got:
                self.data += chunk
                self.got.notify_all()
        with self.got:
            self.got.notify_all()

    def wait(self, needle, start=0):
        with self.got:
            self.got.wait_for(lambda: needle in self.data[start:] or not self.thread.is_alive(), WAIT)
            return needle in self.data[start:]

    def text(self):
        with self.got:
            return bytes(self.data).decode("utf-8", "replace")


class _Pull:
    """A running streamlink stand-in, by default in the relay's fan-out form."""

    def __init__(self, target, argv=None):
        self.proc = subprocess.Popen(argv or relay.streamlink_fanout_cmd(target), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.out, self.err = _Reader(self.proc.stdout), _Reader(self.proc.stderr)

    @property
    def data(self):
        return self.out.data

    def wait_bytes(self, needle, start=0):
        return self.out.wait(needle, start)

    def wait_err(self, needle, start=0):
        return self.err.wait(needle.encode(), start)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=WAIT)
        self.out.thread.join(WAIT)
        self.err.thread.join(WAIT)
        self.proc.stdout.close()
        self.proc.stderr.close()


_alive = stream_stub.pid_alive


def _eventually(pred):
    deadline = time.monotonic() + WAIT
    tick = threading.Event()
    while not pred():
        if time.monotonic() > deadline:
            return False
        tick.wait(0.1)
    return True


# --- the argv contract: every command src/ builds parses in the stand-ins ---

def _ytdlp_argvs():
    url = "sim://comms1"
    out = [relay.ytdlp_resolve_cmd(url, None), relay.ytdlp_resolve_cmd(url, "/x/yt-cookies.txt"),
           relay.ytdlp_resolve_cmd(url, None, relay.quality_ytdlp_fmt("emergency")),
           relay.ytdlp_resolve_cmd(url, None, relay.PREVIEW_FMT_YT),
           relay.ytdlp_live_status_cmd(url, None), relay.ytdlp_live_status_cmd(url, "/x/c.txt")]
    seen = []

    class R:
        returncode, stdout, stderr = 1, "", ""

    def record(cmd, **_kw):
        seen.append(list(cmd))
        return R()
    real = relay.subprocess.run
    relay.subprocess.run = record
    try:
        relay.yt_live_video_lookup(url, "/x/c.txt")
        relay.yt_channel_id(url, None)
    finally:
        relay.subprocess.run = real
    assert len(seen) == 3, seen
    return out + seen + [["yt-dlp", "--version"]]


def t_every_ytdlp_command_of_the_relay_parses():
    for argv in _ytdlp_argvs():
        assert argv[0] == "yt-dlp"
        opts = stream_stub.parse_ytdlp(argv[1:])
        assert opts["url"] or opts["version"], argv
    resolve = stream_stub.parse_ytdlp(relay.ytdlp_resolve_cmd("sim://c", "/j")[1:])
    assert resolve["get_url"] and resolve["prints"] == ["rcq %(height)s %(fps)s"] and resolve["url"] == "sim://c"


def t_every_streamlink_command_of_the_relay_parses():
    relay._STREAMLINK_HELP = stream_stub.STREAMLINK_HELP
    target = "http://sim.invalid/c/360.m3u8"
    argvs = [relay.streamlink_fanout_cmd(target), relay.streamlink_fanout_cmd(target, cookies="/j", tier="robust"),
             relay.streamlink_serve_cmd(target, 53001), relay.streamlink_serve_cmd(target, 53001, cookies="/j"),
             relay.streamlink_serve_cmd(target, 53001, platform="twitch", twitch_token="t", tier="emergency"),
             relay.streamlink_fanout_cmd(target, platform="twitch", twitch_token="t"),
             relay.preview_pull_streamlink_cmd(target, "youtube", relay.PREVIEW_QUALITY_YT, cookies="/j"),
             loopstream.streamlink_argv(target, "53001"),
             loopstream.streamlink_argv(target, "53001", platform="twitch", twitch_token="t"),
             ["streamlink", "--version"], ["streamlink", "--help"]]
    for argv in argvs:
        assert argv[0] == "streamlink"
        opts = stream_stub.parse_streamlink(argv[1:])
        assert opts["target"] or opts["version"] or opts["help"], argv
    assert any("queue" in a for a in relay.streamlink_fanout_cmd(target)), "the help text advertises a deadline flag"
    fan = stream_stub.parse_streamlink(relay.streamlink_fanout_cmd(target)[1:])
    assert fan["stdout"] and fan["target"] == target and fan["quality"] == "best"
    serve = stream_stub.parse_streamlink(loopstream.streamlink_argv(target, "53002")[1:])
    assert serve["http_port"] == 53002 and serve["target"] == target


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _other_tool_argvs():
    """Every yt-dlp/streamlink argv racecast's smoke probes, get-media and the cookie
    exports (get-cookies and the relay's) build."""
    seen = []
    racecast = _load("stubracecast", ("src", "racecast.py"))

    def capture(argv, timeout=30):
        seen.append(list(argv))
        return 1, ""
    racecast._smoke_capture = capture
    racecast._smoke_fingerprint()
    racecast._smoke_js_runtime("https://www.youtube.com/watch?v=abc")
    racecast._smoke_yt_candidates("gt7 endurance")
    racecast._smoke_yt_probe("https://www.youtube.com/watch?v=abc", "/x/yt-cookies.txt")
    racecast._smoke_twitch_probe("somechannel")
    media = _load("stubgetmedia", ("src", "relay", "get-media.py"))
    jar = os.path.join(tempfile.mkdtemp(prefix="stub1231-"), "yt-cookies.txt")
    open(jar, "w").close()
    seen += [media.build_music_cmd("sim://c", os.path.join("x", "intermission.mp3"), jar),
             media.build_download_cmd("sim://c", os.path.join("x", "clip.mp4"), jar)]

    class Done:
        returncode, stderr = 1, b""

    def run(argv, **_kw):
        seen.append(list(argv))
        return Done()
    cookies = _load("stubgetcookies", ("src", "relay", "get-cookies.py"))
    real = subprocess.run   # one module object: patching it patches every importer
    subprocess.run = run
    argv = sys.argv
    sys.argv = ["get-cookies.py", "firefox", "--runtime-dir", tempfile.mkdtemp(prefix="stub1231-")]
    try:
        try:
            cookies.main()
        except SystemExit:
            pass  # the export "failed": only its argv matters here
        relay.export_cookies("firefox", os.path.join(tempfile.mkdtemp(prefix="stub1231-"), "yt-cookies.txt"))
    finally:
        sys.argv = argv
        subprocess.run = real
    return seen


def t_every_other_tool_command_parses():
    argvs = _other_tool_argvs()
    tools = [os.path.basename(a[0]) for a in argvs]
    assert tools.count("streamlink") == 2 and tools.count("yt-dlp") >= 8, tools
    for argv in argvs:
        tool = os.path.basename(argv[0])
        if tool == "yt-dlp":
            opts = stream_stub.parse_ytdlp(argv[1:])
            assert opts["url"] or opts["version"], argv
        elif tool == "streamlink":
            opts = stream_stub.parse_streamlink(argv[1:])
            assert opts["target"] or opts["version"], argv


def t_an_unknown_flag_is_refused_like_argparse():
    for parse, argv in ((stream_stub.parse_ytdlp, ["--exec", "x", "--", "sim://c"]),
                        (stream_stub.parse_streamlink, ["--player", "vlc", "--", "sim://c", "best"]),
                        (stream_stub.parse_streamlink, ["--stdout", "--", "sim://c", "best", "extra"]),
                        (stream_stub.parse_streamlink, ["--stdout", "--bogus", "sim://c"]),
                        (stream_stub.parse_ytdlp, ["--version", "--bogus"])):
        try:
            parse(argv)
        except stream_stub.Usage:
            continue
        raise AssertionError(f"{argv} must be refused")


def t_streamlink_maps_back_only_urls_the_stub_issued():
    issued = stream_stub.resolved_url("comms1", 360)
    assert stream_stub.resolved_channel(issued) == ("comms1", 360)
    assert stream_stub.resolved_channel("sim://comms1") == ("comms1", 360)
    assert stream_stub.resolved_channel(stream_stub.resolved_url("comms1", 1080)) == ("comms1", 360)
    for url in ("http://example.com/comms1/360.m3u8", "https://sim.invalid/comms1/360.m3u8",
                "http://sim.invalid:80/comms1/360.m3u8", "http://user@sim.invalid/comms1/360.m3u8",
                "http://sim.invalid/comms1/360.m3u8?x=1", "http://sim.invalid/../360.m3u8",
                "http://sim.invalid/comms1/x.m3u8", "http://sim.invalid/comms1/360.m3u8#x", "http://sim.invalid.example.com/comms1/360.m3u8"):
        assert stream_stub.resolved_channel(url) is None, url


def t_ytdlp_downloads_are_refused():
    out = tempfile.mkdtemp(prefix="stub1231-")
    stub = _stub()
    stub.channel("comms1")
    for argv in (["-x", "--audio-format", "mp3", "--no-warnings", "-o", os.path.join(out, "i.%(ext)s"), "--",
                  "sim://comms1"],
                 ["-f", "b[height<=1080]/b", "--merge-output-format", "mp4", "--no-warnings", "--force-overwrites",
                  "--no-continue", "-o", os.path.join(out, "clip.mp4"), "--", "sim://comms1"]):
        o, e = io.StringIO(), io.StringIO()
        assert stream_stub.ytdlp_main(stub.directory, argv, o, e) == 1 and "cannot be downloaded" in e.getvalue()
    assert os.listdir(out) == [], "nothing is written"


def t_posix_stand_in_refuses_an_unquotable_interpreter():
    for bad in ('/opt/py"x/python3', "/opt/$HOME/python3", "/opt/a\\b/python3"):
        try:
            stream_stub.posix_stand_in("pass\n", bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    body = stream_stub.posix_stand_in("pass\n", "/opt/my python/bin/python3").decode()
    assert body.startswith("#!/bin/sh\n'''exec' \"/opt/my python/bin/python3\" \"$0\" \"$@\"\n"), body


# --- the control state and the pure decisions ---

def t_pull_action_follows_status_cuts_and_windows():
    ch = {"status": "live", "cuts": [], "stall": None, "freeze": None}
    act = stream_stub.pull_action
    assert act(ch, 100, 200) == "play"
    assert act(None, 100, 200) == "end", "an unknown channel is not live"
    assert act(dict(ch, status="offline"), 100, 200) == "end"
    assert act(dict(ch, cuts=[150]), 100, 200) == "cut", "a cut ends the pull it hit"
    assert act(dict(ch, cuts=[150]), 160, 200) == "play", "a pull started after the cut is not cut again"
    assert act(dict(ch, cuts=[250]), 100, 200) == "play", "a scheduled cut waits for its time"
    assert act(dict(ch, stall={"at": 150, "until": None}), 100, 200) == "stall"
    assert act(dict(ch, stall={"at": 150, "until": 180}), 100, 200) == "play", "a stall window ends"
    assert act(dict(ch, stall={"at": 150, "until": None}), 190, 200) == "stall", "a re-pull in the window stalls too"
    assert act(dict(ch, freeze={"at": 0, "until": None}), 100, 200) == "freeze"
    assert act(dict(ch, freeze={"at": 0, "until": None}, stall={"at": 0, "until": None}), 100, 200) == "stall"


def t_control_api_writes_what_the_stand_ins_read():
    now = [1000.0]
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-"), clock=lambda: now[0]).install()
    a = stub.channel("comms1", stint=1, commentator="Anna")
    b = stub.channel("comms2", stint=2, commentator="Ben", status="upcoming")
    assert a["code"] != b["code"] and a["status"] == "live" and b["status"] == "upcoming"
    assert stub.cut("comms1") == 1000.0
    stub.stall("comms1", at=1010, until=1020)
    stub.freeze("comms1")
    stub.channel("comms1", commentator="Anja")
    ch = stream_stub.load_channel(stub.directory, "comms1")
    assert (ch["cuts"], ch["stall"], ch["freeze"]) == ([1000.0], {"at": 1010, "until": 1020},
                                                       {"at": 1000.0, "until": None})
    assert ch["commentator"] == "Anja" and ch["stint"] == 1, "an update keeps the other fields"
    stub.clear("comms1")
    ch = stream_stub.load_channel(stub.directory, "comms1")
    assert ch["freeze"] is None and ch["stall"] is None, "clear ends running and scheduled windows"
    again = stream_stub.StreamStub(stub.directory).channel("comms3")
    assert again["code"] not in (a["code"], b["code"]), "a second controller keeps the codes unique"
    for bad in ({"name": "sim://x"}, {"name": "../x"}, {"name": ""}, {"name": "x" * 65},
                {"name": "ok", "status": "gone"}, {"name": "ok", "stint": 256}, {"name": "ok", "stint": "2"}):
        try:
            stub.channel(**bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} must be refused")
    try:
        stub.cut("nobody")
    except ValueError:
        pass  # refused as it should be
    else:
        raise AssertionError("a fault needs an existing channel")


def t_identity_strip_round_trips_through_a_frame():
    w, h = 640, 360
    for code, stint in ((1, 0), (173, 3), (255, 255)):
        px = bytearray(w * h * 3)
        for x, y, bw, bh, white in stream_stub.identity_boxes(w, h, code, stint):
            v = 255 if white else 0
            for yy in range(y, y + bh):
                px[(yy * w + x) * 3:(yy * w + x + bw) * 3] = bytes((v,)) * (bw * 3)
        assert stream_stub.decode_identity(bytes(px), w, h) == (code, stint)
    assert stream_stub.decode_identity(bytes(w * h * 3), w, h) is None, "a black frame carries no identity"
    assert stream_stub.decode_identity(b"\xff" * (w * h * 3), w, h) is None, "a white frame fails the end marker"
    x, y, bw, bh, _white = stream_stub.identity_boxes(w, h, 173, 3)[4]
    for yy in range(y, y + bh):
        px[(yy * w + x) * 3:(yy * w + x + bw) * 3] = b"\x80" * (bw * 3)
    assert stream_stub.decode_identity(bytes(px), w, h) is None, "a grey cell is not read as a bit"


def t_ffmpeg_argv_is_conservative():
    argv = stream_stub.ffmpeg_argv(["ffmpeg"], "play", 360, 7, 2, text=True, offset=3.5)
    joined = " ".join(argv)
    for part in ("-nostdin", "-re", "mpeg2video", "mp2", "mpegts", "testsrc2", "textfile=label.txt",
                 "expansion=none", "-output_ts_offset 3.500", "sine=frequency="):
        assert part in joined, part
    assert argv[-1] == "pipe:1"
    frozen = " ".join(stream_stub.ffmpeg_argv(["ffmpeg"], "freeze", 360, 7, 2, text=False, offset=0))
    assert "smptebars" in frozen and "testsrc2" not in frozen and "drawtext" not in frozen
    assert "size=640x360" in frozen


def _ts_packet(pid, cc, pts=None, payload=True):
    """One 188-byte MPEG-TS packet; with `pts`, it starts a PES carrying that PTS (90 kHz)."""
    head = bytes((0x47, (0x40 if pts is not None else 0) | (pid >> 8), pid & 0xFF, (0x10 if payload else 0x20) | cc))
    body = b""
    if pts is not None:
        p = pts
        body = bytes((0, 0, 1, 0xE0, 0, 0, 0x80, 0x80, 5, 0x21 | ((p >> 29) & 0x0E), (p >> 22) & 0xFF,
                      0x01 | ((p >> 14) & 0xFE), (p >> 7) & 0xFF, 0x01 | ((p << 1) & 0xFE)))
    if not payload:
        return head + bytes((183, 0)) + b"\xff" * 182
    return head + body + b"\xff" * (184 - len(body))


def t_splicer_forwards_whole_packets_and_keeps_counters_and_time_rising():
    sp = stream_stub.TsSplicer(fps=25)
    assert sp.next_offset(0.0) == 0.0, "the first run starts at the wall clock"
    run1 = _ts_packet(256, 0, pts=126000) + _ts_packet(256, 1, pts=129600) + _ts_packet(256, 2, pts=133200)
    sp.start(0.0)
    out = sp.feed(run1[:100]) + sp.feed(run1[100:400]) + sp.feed(run1[400:500])
    assert out == run1[:376], "only whole packets pass, the rest waits"
    off = sp.next_offset(0.01)
    assert abs(off - ((129600 - 126000) / 90000 + 0.08)) < 1e-9, "two frames after the last PTS sent"
    sp.start(off)   # the run was killed mid-packet: its tail is dropped
    early = 126000 - 3600 + round(off * 90000)   # this run starts a frame earlier than the first
    run2 = _ts_packet(256, 0, pts=early) + _ts_packet(256, 1, payload=False)
    out = list(stream_stub.parse_ts(sp.feed(run2)))
    assert [p.cc for p in out] == [2, 2], "counters continue, a packet without payload keeps its counter"
    assert out[0].pts > 129600, "time keeps rising across the seam"
    assert abs(sp.next_offset(0.0) - (early / 90000 - (122400 / 90000) + 0.08)) < 1e-6, \
        "the earliest run start seen sets the room for the next one"
    assert sp.next_offset(100.0) == 100.0, "after a long gap the wall clock wins"
    raw = stream_stub.TsSplicer()
    raw.start(0.0)
    assert raw.feed(b"PLAY" * 3) == b"PLAY" * 3, "bytes that are not MPEG-TS pass unchanged"


def t_a_256th_channel_is_refused():
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-")).install()
    for n in range(255):
        stub.channel(f"c{n}")
    try:
        stub.channel("one-too-many")
    except ValueError as e:
        assert "255" in str(e), e
    else:
        raise AssertionError("the identity strip holds 255 codes")
    stub.channel("c7", stint=2)


def t_cli_install_stores_the_stream_timeout():
    d = tempfile.mkdtemp(prefix="stub1231-")
    out = io.StringIO()
    real = sys.stdout
    sys.stdout = out
    try:
        assert stream_stub.main([d, "install", "--stream-timeout", "5"]) == 0
    finally:
        sys.stdout = real
    with open(os.path.join(d, "stub.json")) as fh:
        assert json.load(fh)["stream_timeout"] == 5.0
    stream_stub.StreamStub(d, stream_timeout=7).install()
    with open(os.path.join(d, "stub.json")) as fh:
        assert json.load(fh)["stream_timeout"] == 7, "StreamStub sets it too"


def t_ytdlp_stand_in_answers_the_relay_resolve():
    stub = _stub()
    stub.channel("comms1", stint=1, commentator="Anna")
    restore = _on_path(stub)
    try:
        hls, err, quality = relay.resolve_hls("sim://comms1", None, LOG)
        assert err is None and quality == "360p25", (hls, err, quality)
        assert stream_stub.resolved_channel(hls) == ("comms1", 360)
        hls, err, quality = relay.resolve_hls("sim://comms1", None, LOG, relay.PREVIEW_FMT_YT)
        assert stream_stub.resolved_channel(hls) == ("comms1", 360)
        assert relay.ytdlp_live_status("sim://comms1", None) == "is_live"
        for status, state in (("offline", "not_live_yet"), ("upcoming", "not_live_yet"), ("ended", "ended")):
            stub.channel("comms1", status=status)
            hls, err, _q = relay.resolve_hls("sim://comms1", None, LOG)
            assert hls is None and relay.classify_source_state(err) == state, (status, err)
        assert relay.ytdlp_live_status("sim://comms1", None) == "was_live"
        hls, err, _q = relay.resolve_hls("sim://nobody", None, LOG)
        assert hls is None and relay.classify_source_state(err) == "not_live_yet", err
        hls, err, _q = relay.resolve_hls("https://www.youtube.com/watch?v=abc", None, LOG)
        assert hls is None and "sim://" in err, "a real URL is never resolved"
        assert relay.yt_live_video_lookup("sim://comms1", None) is None, "the chat lookup wraps the name: refused"
        stub.channel("comms1", status="live")
        jar = os.path.join(stub.directory, "jar.txt")
        r = subprocess.run(["yt-dlp", "--cookies-from-browser", "firefox", "--cookies", jar, "--skip-download",
                            "--no-warnings", "sim://comms1"], capture_output=True, text=True,
                           errors="replace", timeout=WAIT)
        assert r.returncode == 1 and not os.path.exists(jar), "no cookie export in simulation"
        assert preflight.tool_version("yt-dlp").startswith("20")
    finally:
        restore()


def t_streamlink_stand_in_version_and_help():
    stub = _stub()
    restore = _on_path(stub)
    try:
        line = preflight.tool_version("streamlink")
        assert preflight.parse_streamlink_version(line) >= preflight.PF_MIN_STREAMLINK, line
        relay._STREAMLINK_HELP = None
        assert relay.queue_deadline_args(relay._streamlink_help()), "the stand-in advertises a deadline flag"
        r = subprocess.run(["streamlink", "--stdout", "--", "https://www.youtube.com/watch?v=abc", "best"],
                           capture_output=True, text=True, errors="replace", timeout=WAIT)
        assert r.returncode == 1 and "No plugin can handle URL" in r.stderr, r.stderr
        r = subprocess.run(["streamlink", "--stdout", "--bogus", "--", "sim://c", "best"],
                           capture_output=True, text=True, errors="replace", timeout=WAIT)
        assert r.returncode == 2 and "unrecognized arguments" in r.stderr, r.stderr
    finally:
        restore()


def t_streamlink_stand_in_refuses_an_offline_channel():
    stub = _stub()
    stub.channel("comms1", status="offline")
    restore = _on_path(stub)
    try:
        r = subprocess.run(relay.streamlink_fanout_cmd("http://sim.invalid/comms1/360.m3u8"),
                           capture_output=True, timeout=WAIT)
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 1 and not r.stdout and relay.classify_source_state(err) == "not_live_yet", err
    finally:
        restore()


def t_pull_plays_freezes_stalls_and_cuts():
    stub = _stub()
    stub.channel("comms2", stint=2, commentator="Ben")
    restore = _on_path(stub)
    pull = _Pull("http://sim.invalid/comms2/360.m3u8")
    try:
        assert pull.wait_bytes(b"PLAY"), pull.err.text()
        assert pull.wait_err("Opening stream: live"), pull.err.text()
        assert relay.parse_stream_quality(pull.err.text()) == "live"
        mark = len(pull.data)
        stub.freeze("comms2")
        assert pull.wait_bytes(b"FREEZE", mark), "the frozen picture follows on the same stdout"
        stub.clear("comms2")
        mark = len(pull.data)
        assert pull.wait_bytes(b"PLAY", mark)
        stub.stall("comms2")
        assert pull.wait_err("[sim][info] stall"), pull.err.text()
        err_mark = len(pull.err.data)
        stub.clear("comms2")
        assert pull.wait_err("[sim][info] play", err_mark), pull.err.text()
        mark = len(pull.data)
        assert pull.wait_bytes(b"PLAY", mark), "bytes flow again after the stall"
        stub.cut("comms2")
        assert pull.wait_err("Stream ended"), pull.err.text()
        assert pull.proc.wait(timeout=WAIT) == 0, "a cut ends the pull like a finished stream"
    finally:
        pull.close()
        restore()
    restore = _on_path(stub)
    again = _Pull("http://sim.invalid/comms2/360.m3u8")
    try:
        assert again.wait_bytes(b"PLAY"), "the next pull after a cut plays"
        stub.channel("comms2", status="offline")
        assert again.proc.wait(timeout=WAIT) == 0, "going offline ends the running pull"
    finally:
        again.close()
        restore()


def t_a_stalled_pull_gives_up_after_the_stream_timeout():
    stub = _stub(stream_timeout=0)
    stub.channel("comms1")
    stub.stall("comms1")
    restore = _on_path(stub)
    try:
        r = subprocess.run(relay.streamlink_fanout_cmd("sim://comms1"), capture_output=True, timeout=WAIT)
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 1 and not r.stdout and "Read timeout" in err, err
        assert relay.classify_source_state(err) is None, "a read timeout is a plain drop"
    finally:
        restore()


def t_an_unreadable_state_file_keeps_the_last_state():
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-")).install()
    stub.channel("comms1", commentator="Anna")
    lines = []
    pull = stream_stub.Pull(stub.directory, "comms1", 360, ["ffmpeg"], None, lines.append)
    assert pull.action() == "play"
    with open(stream_stub.channel_path(stub.directory, "comms1"), "w") as fh:
        fh.write("{not json")
    assert pull.action() == "play" and pull.channel()["commentator"] == "Anna", "a torn write is not an end"
    assert lines == ["[sim][warning] channel state unreadable, keeping the last one"], lines
    os.remove(stream_stub.channel_path(stub.directory, "comms1"))
    assert pull.action() == "end", "a removed channel ends the pull"
    stub.channel("comms1")
    with open(stream_stub.channel_path(stub.directory, "comms1"), "w") as fh:
        fh.write("{not json")
    opened = stream_stub.Pull(stub.directory, "comms1", 360, ["ffmpeg"], None, lines.append,
                              channel={"name": "comms1", "code": 1, "status": "live"})
    assert opened.action() == "play", "a first read that fails keeps the state the pull opened with"
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("sharing violation")
        return "read"
    assert stream_stub._retry_permission(flaky) == "read" and len(tries) == 3, "a sharing violation is retried"


def t_ffmpeg_gets_the_label_and_identity():
    stub = _stub()
    stub.channel("comms3", stint=3, commentator="Cleo")
    restore = _on_path(stub)
    pull = _Pull("sim://comms3")
    try:
        assert pull.wait_bytes(b"ARGV-END"), pull.err.text()
        text = bytes(pull.data).decode("utf-8", "replace")
        assert "LABEL[STINT 3\nCleo\nsim://comms3]" in text, text[:400]
        assert "testsrc2" in text and "size=640x360" in text
    finally:
        pull.close()
        restore()


def t_a_failing_text_overlay_falls_back_to_no_text():
    stub = _stub(ffmpeg=[sys.executable, FAKE_FFMPEG, "--fail-drawtext"])
    stub.channel("comms1", stint=1, commentator="Anna")
    restore = _on_path(stub)
    pull = _Pull("sim://comms1")
    try:
        assert pull.wait_bytes(b"PLAY"), pull.err.text()
        assert "without text" in pull.err.text(), pull.err.text()
    finally:
        pull.close()
        restore()


def t_killing_the_stand_in_ends_its_ffmpeg():
    stub = _stub()
    stub.channel("comms1")
    restore = _on_path(stub)
    pull = _Pull("sim://comms1")
    try:
        assert pull.wait_bytes(b"PLAY"), pull.err.text()
        with open(os.path.join(stub.directory, "fake-ffmpeg.pid")) as fh:
            pid = int(fh.read())
        assert _alive(pid)
        pull.proc.kill()
        pull.proc.wait(timeout=WAIT)
        assert _eventually(lambda: not _alive(pid)), "the orphaned ffmpeg stops on its broken pipe"
        assert os.listdir(os.path.join(stub.directory, "pulls")), "a killed stand-in leaves its work dir"
        stub.install()
        assert os.listdir(os.path.join(stub.directory, "pulls")) == [], "install clears what killed pulls left"
    finally:
        pull.close()
        restore()


def t_a_closed_consumer_ends_the_pull():
    stub = _stub()
    stub.channel("comms1")
    restore = _on_path(stub)
    proc = subprocess.Popen(relay.streamlink_fanout_cmd("sim://comms1"), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout.read(4), "bytes flow"
        proc.stdout.close()
        assert proc.wait(timeout=WAIT) == 1, "the stand-in exits once nobody reads its stdout"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=WAIT)
        restore()


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port, size=4096):
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=WAIT) as c:
        c.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = b""
        while len(data) < size:
            chunk = c.recv(65536)
            if not chunk:
                break
            data += chunk
        return data


def t_direct_serve_reserves_each_player_like_streamlink():
    stub = _stub()
    stub.channel("comms1")
    port = _free_port()
    restore = _on_path(stub)
    pull = _Pull(None, relay.streamlink_serve_cmd("http://sim.invalid/comms1/360.m3u8", port))
    try:
        assert pull.wait_err("Starting server"), pull.err.text()
        assert b"PLAY" in _get(port), "a player gets the stream"
        got, done = [], threading.Event()

        def player():
            got.append(_get(port, 1 << 30))
            done.set()
        threading.Thread(target=player, daemon=True).start()
        assert _eventually(lambda: pull.err.text().count("Got HTTP request from player") >= 2), pull.err.text()
        stub.cut("comms1")
        assert done.wait(WAIT) and b"PLAY" in got[0], "a cut ends that player's stream"
        assert pull.proc.poll() is None, "the server keeps running after the stream ends"
        assert b"PLAY" in _get(port), "the next player gets a fresh pull"
        stub.channel("comms1", status="offline")
        assert _get(port) == b"", "an offline channel closes the player without data"
        assert pull.proc.poll() is None
    finally:
        pull.close()
        restore()


def t_direct_serve_binds_loopback_only_and_alone():
    import socket
    sock = stream_stub.listen(0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
        if os.name == "nt":
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
        else:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
    finally:
        sock.close()
    stub = _stub()
    stub.channel("comms1")
    restore = _on_path(stub)
    busy = socket.socket()
    try:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        r = subprocess.run(relay.streamlink_serve_cmd("sim://comms1", busy.getsockname()[1]),
                           capture_output=True, text=True, errors="replace", timeout=WAIT)
        assert r.returncode == 1 and "Failed to start HTTP server" in r.stderr, r.stderr
    finally:
        busy.close()
        restore()


def t_ts_parse_reads_pid_cc_pts_pcr_and_offset():
    pcr = bytes((0x47, 0x01, 0x00, 0x20, 183, 0x10, 0x12, 0x34, 0x56, 0x78, 0x80)) + b"\xff" * 177
    data = _ts_packet(256, 3, pts=90000 * 2) + b"\x00" * 188 + _ts_packet(256, 4) + pcr + \
        _ts_packet(257, 0, pts=(1 << 33) - 1)
    assert [(p.at, p.pid, p.cc, p.pts, p.pcr) for p in stream_stub.parse_ts(data)] == [
        (0, 256, 3, 180000, None), (376, 256, 4, None, None), (564, 256, 0, None, 0x12345678 << 1 | 1),
        (752, 257, 0, (1 << 33) - 1, None)]
    sp = stream_stub.TsSplicer()
    sp.start(0.0)
    sp.feed(_ts_packet(256, 5))
    sp.start(0.0)
    out = sp.feed(_ts_packet(256, 0) + b"\x00" * 188 + _ts_packet(256, 1))
    assert out[188:376] == b"\x00" * 188, "a packet without sync is left alone"
    assert [p.cc for p in stream_stub.parse_ts(out)] == [6, 7], "counters land on their own packets"


def t_reinstall_over_a_running_pull():
    stub = _stub()
    stub.channel("comms1")
    restore = _on_path(stub)
    pull = _Pull("sim://comms1")
    try:
        assert pull.wait_bytes(b"PLAY"), pull.err.text()
        stub.install()
        shutil.rmtree(os.path.join(stub.directory, "pulls"))
        mark = len(pull.data)
        stub.freeze("comms1")
        assert pull.wait_bytes(b"FREEZE", mark), pull.err.text()
        assert pull.proc.poll() is None, "the pull survives a reinstall and a lost work dir"
    finally:
        pull.close()
        restore()
    stub.install()
    assert not glob.glob(os.path.join(stub.directory, "*.old")), "a later install removes moved-aside stand-ins"


def t_install_prunes_only_dead_pulls():
    stub = _stub()
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=WAIT)
    live = os.path.join(stub.directory, "pulls", f"{os.getpid()}-1")
    dead = os.path.join(stub.directory, "pulls", f"{gone.pid}-1")
    for d in (live, dead):
        os.makedirs(d)
    stub.install()
    assert os.path.isdir(live) and not os.path.exists(dead)


def t_a_zombie_is_not_alive():
    if os.name == "nt":
        raise Skip("no zombies on Windows")
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        assert _eventually(lambda: not stream_stub.pid_alive(p.pid)), "an exited, unreaped child is gone"
    finally:
        p.wait(timeout=WAIT)


def t_names_are_case_insensitive_and_never_devices():
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-")).install()
    a = stub.channel("Comms1", stint=1)
    assert a["name"] == "comms1" and stub.channel("COMMS1")["code"] == a["code"], "one channel, any case"
    assert stream_stub.resolved_channel("sim://Comms1") == ("comms1", 360)
    assert stream_stub.resolved_channel(stream_stub.resolved_url("COMMS1", 360)) == ("comms1", 360)
    assert stream_stub.channel_name("comms1\n") is None, "a trailing newline is not a name"
    for bad in ("con", "NUL", "Prn", "aux", "com1", "COM9", "lpt1", "LpT9"):
        assert stream_stub.channel_name(bad) is None, bad
        assert stream_stub.resolved_channel("sim://" + bad) is None, bad
    assert stream_stub.channel_name("com10") == "com10" and stream_stub.channel_name("console") == "console"
    o, e = io.StringIO(), io.StringIO()
    assert stream_stub.ytdlp_main(stub.directory, ["-g", "--", "sim://COMMS1"], o, e) == 0, e.getvalue()
    assert stream_stub.resolved_channel(o.getvalue().strip()) == ("comms1", 360)


def t_relative_ffmpeg_is_made_absolute():
    d = tempfile.mkdtemp(prefix="stub1231-")
    assert stream_stub.StreamStub(d, ffmpeg=[os.path.join("bin", "ffmpeg")]).ffmpeg[0] == os.path.abspath(
        os.path.join("bin", "ffmpeg"))
    assert stream_stub.StreamStub(d, ffmpeg=["ffmpeg"]).ffmpeg == ["ffmpeg"], "a bare name stays a PATH lookup"


_RACE = """
import sys
sys.path.insert(0, sys.argv[1])
import stream_stub
stub = stream_stub.StreamStub(sys.argv[2])
for n in range(int(sys.argv[4])):
    stub.channel(f"{sys.argv[3]}{n}")
    stub.cut("shared")
"""


def t_two_controllers_share_a_directory():
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-")).install()
    stub.channel("shared")
    fakes = os.path.dirname(stream_stub.__file__)
    procs = [subprocess.Popen([sys.executable, "-c", _RACE, fakes, stub.directory, prefix, "12"])
             for prefix in ("a", "b")]
    for p in procs:
        assert p.wait(timeout=WAIT * 2) == 0
    codes = [c["code"] for c in stub.channels().values()]
    assert len(codes) == 25 and len(set(codes)) == 25, "every channel gets its own code"
    assert len(stub.get("shared")["cuts"]) == 24, "no cut is lost to a concurrent write"


def t_a_held_lock_blocks_and_a_stale_one_is_broken():
    stub = stream_stub.StreamStub(tempfile.mkdtemp(prefix="stub1231-")).install()
    done = threading.Event()

    def create():
        stub.channel("late")
        done.set()
    with stream_stub.state_lock(stub.directory):
        t = threading.Thread(target=create, daemon=True)
        t.start()
        assert not done.wait(0.5), "a change waits for the lock"
    assert done.wait(WAIT), "and goes ahead once it is free"
    lock = os.path.join(stub.directory, "channels", ".lock")
    open(lock, "w").close()
    old = time.time() - stream_stub.LOCK_STALE - 5
    os.utime(lock, (old, old))
    stub.channel("after-crash")
    assert not os.path.exists(lock), "a lock left by a crashed controller is broken"


def t_windows_stand_in_is_a_launcher_with_a_zipped_main():
    try:
        launcher = stream_stub.windows_launcher()
    except stream_stub.LauncherMissing as why:
        raise Skip(str(why)) from None
    d = tempfile.mkdtemp(prefix="stub1231-")
    stub = stream_stub.StreamStub(d, windows=True).install()
    exe = os.path.join(stub.directory, "yt-dlp.exe")
    with open(exe, "rb") as fh:
        body = fh.read()
    assert body.startswith(launcher) and body[:2] == b"MZ"
    shebang = body[len(launcher):].split(b"\n", 1)[0]
    assert shebang.startswith(b"#!") and os.path.basename(sys.executable).encode() in shebang
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert zf.namelist() == ["__main__.py"]
    r = subprocess.run([sys.executable, exe, "--version"], capture_output=True, text=True,
                       errors="replace", timeout=WAIT)
    assert r.returncode == 0 and r.stdout.startswith("20"), (r.stdout, r.stderr)
    assert stream_stub.launcher_shebang("C:\\Program Files\\Python\\python.exe") == \
        b'#!"C:\\Program Files\\Python\\python.exe"\n', "a path with spaces is quoted like distlib does"
    assert simulation.stand_in_present(stub.directory, "yt-dlp", windows=True)
    assert simulation.stand_in_present(stub.directory, "streamlink", windows=True)


def t_stand_in_runs_from_an_interpreter_path_with_spaces():
    d = tempfile.mkdtemp(prefix="stub1231-")
    spaced = os.path.join(d, "py with space")
    home = os.path.dirname(sys.executable)
    if os.name == "nt":   # a junction needs no admin rights, unlike a symlink
        r = subprocess.run(["cmd", "/c", "mklink", "/J", spaced, home], capture_output=True, text=True,
                           errors="replace", timeout=WAIT)
        if r.returncode != 0:
            raise Skip(f"no junction: {r.stdout} {r.stderr}")
    else:
        os.symlink(home, spaced)
    python = os.path.join(spaced, os.path.basename(sys.executable))
    stub = stream_stub.StreamStub(os.path.join(d, "bin"), python=python).install()
    exe = os.path.join(stub.directory, "yt-dlp" + (".exe" if os.name == "nt" else ""))
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, errors="replace", timeout=WAIT)
    assert r.returncode == 0 and r.stdout.startswith("20"), (r.stdout, r.stderr)


def t_installed_directory_passes_the_simulation_check():
    stub = _stub()
    env = dict(os.environ, **stub.env(), RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s")
    assert simulation.problems(env) == [], simulation.problems(env)


def t_real_ffmpeg_pull_carries_the_identity():
    try:
        ok = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, timeout=WAIT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        assert not os.environ.get("OBS_SIM_REQUIRE_FFMPEG"), "this CI job installs ffmpeg"
        raise Skip("no working ffmpeg on PATH")
    stub = _stub(ffmpeg=["ffmpeg"])
    code = stub.channel("comms2", stint=2, commentator="Ben")["code"]
    restore = _on_path(stub)
    try:
        for mode in ("play", "freeze"):
            if mode == "freeze":
                stub.freeze("comms2")
            pull = subprocess.Popen(relay.streamlink_fanout_cmd("http://sim.invalid/comms2/360.m3u8"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                frame = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mpegts", "-i", "pipe:0",
                                        "-an", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                                       stdin=pull.stdout, capture_output=True, timeout=WAIT)
            finally:
                pull.kill()
                pull.wait(timeout=WAIT)
                err = pull.stderr.read().decode("utf-8", "replace")
                pull.stdout.close()
                pull.stderr.close()
            assert len(frame.stdout) == 640 * 360 * 3, (mode, frame.stderr, err)
            assert stream_stub.decode_identity(frame.stdout, 640, 360) == (code, 2), (mode, err)
            if os.environ.get("OBS_SIM_REQUIRE_FFMPEG"):   # the e2e job's ffmpeg has a default font
                assert "without text" not in err, "drawtext works with the CI ffmpeg: " + err
    finally:
        restore()


def t_real_ffmpeg_seams_keep_time_and_counters_rising():
    try:
        ok = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, timeout=WAIT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        assert not os.environ.get("OBS_SIM_REQUIRE_FFMPEG"), "this CI job installs ffmpeg"
        raise Skip("no working ffmpeg on PATH")
    stub = _stub(ffmpeg=["ffmpeg"])
    stub.channel("comms1", stint=1, commentator="Anna")
    restore = _on_path(stub)
    pull = _Pull("sim://comms1")
    try:
        for step, marker in ((None, "[sim][info] play"), ("freeze", "[sim][info] freeze"), ("clear", "[sim][info] play")):
            err_mark = len(pull.err.data)
            if step == "freeze":
                stub.freeze("comms1")
            elif step == "clear":
                stub.clear("comms1")
            assert pull.wait_err(marker, err_mark), pull.err.text()
            mark = len(pull.data)
            assert pull.out.wait(b"\x47", mark + 188 * 200), "the run after the seam sends packets"
    finally:
        pull.close()
        restore()
    data = bytes(pull.data)
    assert len(data) % 188 == 0, "only whole packets are forwarded"
    last_pts, last_cc, last_pcr = {}, {}, {}
    for p in stream_stub.parse_ts(data):
        if p.pid == 0x1FFF:
            continue
        if p.pts is not None:
            assert p.pts > last_pts.get(p.pid, -1), f"PTS of PID {p.pid} went back: {last_pts[p.pid]} -> {p.pts}"
            last_pts[p.pid] = p.pts
        if p.pcr is not None:
            assert p.pcr > last_pcr.get(p.pid, -1), f"PCR of PID {p.pid} went back"
            last_pcr[p.pid] = p.pcr
        if p.payload and p.pid in last_cc:
            assert p.cc == (last_cc[p.pid] + 1) % 16, f"counter of PID {p.pid} jumped"
        last_cc[p.pid] = p.cc
    assert len(last_pts) >= 2, "video and audio carry PTS"
    assert last_pcr, "the stream carries a PCR"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            try:
                fn()
            except Skip as why:
                print("SKIP", name, "-", why)
                continue
            print("ok", name)
    print("ALL PASS")
