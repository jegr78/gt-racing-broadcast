#!/usr/bin/env python3
"""The event simulation's stream stub (#1231). Run: python3 tests/test_stream_stub.py"""
import importlib.util
import io
import logging
import os
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


def _alive(pid):
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def t_an_unknown_flag_is_refused_like_argparse():
    for parse, argv in ((stream_stub.parse_ytdlp, ["--exec", "x", "--", "sim://c"]),
                        (stream_stub.parse_streamlink, ["--player", "vlc", "--", "sim://c", "best"]),
                        (stream_stub.parse_streamlink, ["--stdout", "--", "sim://c", "best", "extra"])):
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
                "http://sim.invalid/comms1/x.m3u8", "http://sim.invalid.example.com/comms1/360.m3u8"):
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
        pass
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


# --- the stand-ins, run by bare name through PATH like the relay runs them ---

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


def t_direct_serve_answers_on_its_port():
    import socket
    import urllib.request
    stub = _stub()
    stub.channel("comms1")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    restore = _on_path(stub)
    pull = _Pull(None, relay.streamlink_serve_cmd("http://sim.invalid/comms1/360.m3u8", port))
    try:
        assert pull.wait_err("Starting server"), pull.err.text()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=WAIT) as r:  # noqa: S310  loopback test server
            assert b"PLAY" in r.read(4096)
        stub.cut("comms1")
        assert pull.proc.wait(timeout=WAIT) == 0, "a cut ends the serving process"
    finally:
        pull.close()
        restore()


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
            assert "without text" not in err, "drawtext works with the CI ffmpeg: " + err
    finally:
        restore()


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
