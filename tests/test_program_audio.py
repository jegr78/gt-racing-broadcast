#!/usr/bin/env python3
"""Stdlib unit checks for the on-air program-audio monitor (relay tap).
Run: python3 tests/test_program_audio.py"""
import importlib.util, io, logging, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# program_audio_enabled: default ON, an explicit falsey token disables.
def t_program_audio_default_on():
    assert m.program_audio_enabled({}) is True
    assert m.program_audio_enabled({"RACECAST_PROGRAM_AUDIO": ""}) is True
    assert m.program_audio_enabled({"RACECAST_PROGRAM_AUDIO": "1"}) is True
    assert m.program_audio_enabled({"RACECAST_PROGRAM_AUDIO": "on"}) is True


def t_program_audio_killswitch():
    for tok in ("0", "false", "no", "off", "OFF", " Off "):
        assert m.program_audio_enabled({"RACECAST_PROGRAM_AUDIO": tok}) is False


# program_audio_ffmpeg_cmd: audio-only MP3 to stdout, params from the constants.
def t_program_audio_ffmpeg_cmd_shape():
    cmd = m.program_audio_ffmpeg_cmd()
    assert cmd[0] == "ffmpeg"
    assert "-vn" in cmd                       # no video
    assert cmd[cmd.index("-map") + 1] == "0:a:0?"   # optional audio stream
    assert cmd[cmd.index("-ar") + 1] == m.PROGRAM_AUDIO_SAMPLE_RATE
    assert cmd[cmd.index("-ac") + 1] == m.PROGRAM_AUDIO_CHANNELS
    assert cmd[cmd.index("-c:a") + 1] == m.PROGRAM_AUDIO_CODEC
    assert cmd[cmd.index("-b:a") + 1] == m.PROGRAM_AUDIO_BITRATE
    assert cmd[cmd.index("-f") + 1] == m.PROGRAM_AUDIO_FORMAT
    assert cmd[-1] == "pipe:1"                 # emit to stdout


def t_program_audio_defaults_are_mp3():
    assert m.PROGRAM_AUDIO_CODEC == "libmp3lame"
    assert m.PROGRAM_AUDIO_FORMAT == "mp3"
    assert m.PROGRAM_AUDIO_CONTENT_TYPE == "audio/mpeg"


# should_retarget: re-point the encoder only on a real, serving handover.
def t_should_retarget_on_handover():
    assert m.should_retarget("A", "B", True) is True
    assert m.should_retarget("B", "A", True) is True


def t_should_retarget_no_change():
    assert m.should_retarget("A", "A", True) is False


def t_should_retarget_guards():
    assert m.should_retarget("A", "B", False) is False   # new feed not serving yet
    assert m.should_retarget("A", None, True) is False    # no on-air feed
    assert m.should_retarget(None, "A", True) is True     # first target counts


# ProgramAudioService: refcount, idle reaper and handover restart, thread-free.
class _FakeRing:
    def __init__(self):
        self.closed = False
    def live_offset(self):
        return 0
    def read(self, cursor, timeout):
        return b"", cursor          # never yields in tests; we don't run pumps
    def close(self):
        self.closed = True


class _FakeFeed:
    def __init__(self, ring):
        self.ring = ring


class _FakeRelay:
    def __init__(self, fanout=True, live="A"):
        self.fanout = fanout
        self._live = live
        self.feeds = {"A": _FakeFeed(_FakeRing()), "B": _FakeFeed(_FakeRing())}
    def live_feed(self):
        return self._live


class _FakeProc:
    def __init__(self):
        self.killed = False
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
    def poll(self):
        return 0 if self.killed else None
    def kill(self):
        self.killed = True


def _svc(relay, spawns):
    def spawn():
        p = _FakeProc(); spawns.append(p); return p, p.stdin, p.stdout
    return m.ProgramAudioService(relay, _Log(), idle_timeout=0.01,
                                 spawn=spawn, ring_factory=_FakeRing)


class _Log:
    def info(self, *a, **k):
        pass


def t_acquire_none_when_fanout_off():
    svc = _svc(_FakeRelay(fanout=False), [])
    assert svc.acquire() is None
    assert svc._listeners == 0


def t_acquire_returns_output_ring_and_counts():
    svc = _svc(_FakeRelay(), [])
    ring = svc.acquire()
    assert ring is not None and ring is svc._out
    assert svc._listeners == 1
    ring2 = svc.acquire()
    assert ring2 is svc._out            # same shared output ring
    assert svc._listeners == 2
    svc.release(); svc.release()
    assert svc._listeners == 0
    svc.shutdown()


def t_encoder_tick_spawns_for_on_air_feed():
    relay = _FakeRelay(live="A"); spawns = []
    svc = _svc(relay, spawns)
    svc.acquire()
    target = svc._encoder_tick(None)
    assert target == "A"
    assert len(spawns) == 1
    assert svc._enc_target == "A"
    svc.shutdown()


def t_encoder_tick_reencodes_on_handover():
    relay = _FakeRelay(live="A"); spawns = []
    svc = _svc(relay, spawns)
    svc.acquire()
    prev = svc._encoder_tick(None)      # spawns for A
    relay._live = "B"                    # handover
    prev = svc._encoder_tick(prev)      # should kill A's proc, spawn for B
    assert prev == "B"
    assert len(spawns) == 2
    assert spawns[0].killed is True      # old encoder killed
    assert svc._enc_target == "B"
    svc.shutdown()


def t_encoder_tick_noop_when_unchanged():
    relay = _FakeRelay(live="A"); spawns = []
    svc = _svc(relay, spawns)
    svc.acquire()
    prev = svc._encoder_tick(None)
    svc._encoder_tick(prev)             # same feed -> no respawn (return value unused)
    assert len(spawns) == 1
    svc.shutdown()


def t_encoder_tick_respawns_dead_proc():
    # ffmpeg died on its own mid-stint (not a handover): live feed is still "A",
    # should_retarget("A", "A", True) is False, but the current proc has exited
    # -> the tick must still respawn so the output ring doesn't go silent forever.
    relay = _FakeRelay(live="A"); spawns = []
    svc = _svc(relay, spawns)
    svc.acquire()
    prev = svc._encoder_tick(None)      # spawns for A
    assert len(spawns) == 1
    spawns[0].killed = True             # simulate ffmpeg exiting on its own (poll() != None)
    prev = svc._encoder_tick(prev)      # SAME live feed ("A") -> must still respawn
    assert len(spawns) == 2             # a NEW proc was spawned
    assert svc._enc_target == "A"
    assert prev == "A"
    svc.shutdown()


# The teardown re-arm race and the per-generation stdin.
def t_teardown_rearms_when_listener_slips_in():
    # A listener slipped in during the idle-reap window: teardown must NOT close
    # the output ring and must re-arm a fresh supervisor. relay.live_feed()=None
    # so the re-armed supervisor has nothing to encode (stays thread-quiet).
    relay = _FakeRelay(); relay._live = None
    svc = _svc(relay, [])
    out = _FakeRing()
    svc._out = out
    svc._running = True
    svc._listeners = 1
    svc._teardown()
    assert svc._out is out               # the same ring object, not nulled
    assert out.closed is False           # and NOT closed
    assert svc._running is True          # re-armed, still running
    svc.shutdown()
    assert out.closed is True            # genuine shutdown finalizes


def t_teardown_finalizes_when_no_listeners():
    relay = _FakeRelay()
    svc = _svc(relay, [])
    out = _FakeRing()
    svc._out = out
    svc._running = True
    svc._listeners = 0
    svc._teardown()
    assert out.closed is True            # normal idle teardown closes the ring
    assert svc._out is None
    assert svc._running is False


def t_feed_stdin_exits_on_own_dead_proc_after_reassign():
    # The old generation's _feed_stdin must exit when ITS OWN proc dies, even
    # after a handover reassigned self._proc to a new live process. If it checked
    # the shared self._proc (alive), this call would loop forever and hang.
    svc = _svc(_FakeRelay(), [])
    mine = _FakeProc(); mine.kill()          # this thread's own proc is dead
    svc._proc = _FakeProc()                   # handover installed a NEW live proc
    svc._feed_stdin(io.BytesIO(), _FakeRing(), mine)   # must return (checks `mine`)
    assert svc._proc.poll() is None           # the reassigned proc is untouched/alive


# _program_audio_stream_ring: the header contract and streaming loop, thread-free.
class _CapturingWFile:
    def __init__(self):
        self.chunks = []
    def write(self, b):
        self.chunks.append(bytes(b))


class _ScriptRing:
    """Yields a fixed set of chunks then reports closed (ends the stream loop)."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False
    def live_offset(self):
        return 0
    def read(self, cursor, timeout):
        if self._chunks:
            return self._chunks.pop(0), cursor + 1
        self.closed = True
        return b"", cursor


class _FakeHandler:
    """Minimal stand-in exposing just what _stream_ring touches. We bind the real
    unbound method to it so we test the shipped code path."""
    def __init__(self):
        self.status = None
        self.headers_sent = {}
        self.ended = False
        self.wfile = _CapturingWFile()
    def send_response(self, code):
        self.status = code
    def send_header(self, k, v):
        self.headers_sent[k] = v
    def end_headers(self):
        self.ended = True


class _SvcStub:
    def touch(self):
        pass


def t_stream_ring_headers_and_body():
    h = _FakeHandler()
    ring = _ScriptRing([b"MP3a", b"MP3b"])
    # Bind the real _stream_ring implementation onto our fake handler.
    m._program_audio_stream_ring(h, ring, m.PROGRAM_AUDIO_CONTENT_TYPE, _SvcStub())
    assert h.status == 200
    assert h.headers_sent["Content-Type"] == "audio/mpeg"
    assert h.headers_sent["Cache-Control"] == "no-store"
    assert "Content-Length" not in h.headers_sent      # endless stream
    assert h.ended is True
    assert b"".join(h.wfile.chunks) == b"MP3aMP3b"


# _program_audio_is_probe: the ?probe=1 availability check, with no acquire.
def t_program_audio_is_probe_true_only_for_one():
    assert m._program_audio_is_probe("/preview/program-audio?probe=1") is True
    assert m._program_audio_is_probe("/cockpit/program-audio?probe=1&ts=9") is True


def t_program_audio_is_probe_false_otherwise():
    assert m._program_audio_is_probe("/preview/program-audio") is False
    assert m._program_audio_is_probe("/preview/program-audio?ts=123") is False
    assert m._program_audio_is_probe("/preview/program-audio?probe=0") is False
    assert m._program_audio_is_probe("/preview/program-audio?probe=") is False


# ProgramAudioService._join_offset joins at the trailing offset. (#533)
def t_program_audio_join_offset_uses_relay_prebuffer():
    r = m.FeedRing(1_000_000)
    for i in range(10):
        r.write(b"x" * 100, now=float(i))

    class FakeRelay:
        feed_prebuffer_s = 3.0
    svc = m.ProgramAudioService(FakeRelay(), logging.getLogger("t533pa"))
    assert svc._join_offset(r, now=9.0) == 700


def t_program_audio_join_offset_defaults_when_relay_lacks_attr():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 500, now=1.0)

    class FakeRelay:
        pass
    svc = m.ProgramAudioService(FakeRelay(), logging.getLogger("t533pa"))
    assert svc._join_offset(r, now=5.0) == 500   # getattr default 0.0 -> live edge


# fMP4/CMAF joins. A Twitch feed can be fMP4/CMAF rather than MPEG-TS. A
# mid-stream join then lands inside an `mdat` with no ftyp/moov, so ffmpeg has no
# codec parameters and cannot resync. Only the initialization segment PLUS a
# moof-aligned join yields MP3 frames. (#576)

def _box(typ, payload=b""):
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


_FTYP = _box(b"ftyp", b"mp42" + b"\x00" * 8)
_MOOV = _box(b"moov", b"\x11" * 200)
_INIT = _FTYP + _MOOV


def _fragment(payload):
    return _box(b"moof", b"\x22" * 60) + _box(b"mdat", payload)


def t_fmp4_init_segment_ends_after_moov():
    head = _INIT + _fragment(b"\x33" * 400)
    assert m.fmp4_init_segment(head) == _INIT


def t_fmp4_init_segment_without_a_fragment_yet():
    """The head can be captured before the first moof arrives: the init segment
    is complete at the end of moov and must not wait for one."""
    assert m.fmp4_init_segment(_INIT) == _INIT


def t_fmp4_init_segment_refuses_a_truncated_moov():
    """Half a moov is worse than none: it would hand ffmpeg a broken header."""
    assert m.fmp4_init_segment(_INIT[:-50]) == b""


def t_fmp4_init_segment_is_empty_for_mpeg_ts():
    """The TS path must stay byte-identical: no prefix, no alignment."""
    ts = b"".join(b"\x47" + bytes([i % 251]) * 187 for i in range(20))
    assert m.fmp4_init_segment(ts) == b""
    assert m.fmp4_init_segment(b"") == b""
    assert m.fmp4_init_segment(None) == b""


def t_fmp4_fragment_start_finds_the_first_moof():
    stream = _INIT + _fragment(b"\x33" * 400) + _fragment(b"\x44" * 400)
    at = m.fmp4_fragment_start(stream)
    assert at == len(_INIT)
    assert stream[at + 4:at + 8] == b"moof"


def t_fmp4_fragment_start_skips_a_moof_inside_mdat_payload():
    """The search is a validated pattern scan, not a box walk, because a
    mid-stream join lands inside an mdat where sizes are meaningless. Media
    payload containing the four bytes 'moof' must not be mistaken for a box
    start."""
    decoy = b"\x00\x00\x00\x40" + b"moof" + b"\x55" * 300
    stream = _fragment(decoy) + _fragment(b"\x66" * 400)
    at = m.fmp4_fragment_start(stream)
    assert at == 0                                   # the REAL first moof
    at2 = m.fmp4_fragment_start(stream, start=8)     # past it: the decoy is skipped
    assert at2 == len(_fragment(decoy))


def t_fmp4_fragment_start_waits_for_enough_bytes_to_validate():
    """A candidate that cannot be validated yet is 'not found', so the caller
    keeps buffering rather than committing to a guess."""
    stream = _INIT + _box(b"moof", b"\x22" * 60)
    assert m.fmp4_fragment_start(stream) is None


def t_fmp4_aligned_take_holds_until_a_boundary():
    buf = bytearray(b"\x99" * 100)
    data, pending = m.fmp4_aligned_take(buf)
    assert data == b"" and pending is buf             # still searching
    buf += _fragment(b"\x33" * 400)
    data, pending = m.fmp4_aligned_take(buf)
    assert pending is None
    assert data[4:8] == b"moof"                       # the leading garbage is dropped


def t_fmp4_aligned_take_gives_up_and_passes_through():
    """Budget spent: degrade to today's raw behaviour rather than stall forever."""
    buf = bytearray(b"\x99" * (m.FMP4_ALIGN_SCAN_BYTES + 1))
    data, pending = m.fmp4_aligned_take(buf)
    assert pending is None and data == bytes(buf)


def t_feed_ring_keeps_and_resets_the_stream_head():
    """The ring is created ONCE in Relay.start() and outlives every streamlink
    process, so the head must be dropped when the writer starts a new one.
    Otherwise the next stream is served the previous stream's init segment."""
    r = m.FeedRing(1024)                      # smaller than the head budget
    r.write(_INIT)
    r.write(b"\x77" * 4096)                    # scrolls the ring, not the head
    assert r.head().startswith(_INIT)
    assert m.fmp4_init_segment(r.head()) == _INIT
    r.reset_head()
    assert r.head() == b""
    r.write(b"\x47" * 400)
    assert m.fmp4_init_segment(r.head()) == b""


def t_feed_ring_head_is_bounded():
    r = m.FeedRing(1024)
    r.write(b"z" * (m.FEED_HEAD_BYTES + 5000))
    assert len(r.head()) == m.FEED_HEAD_BYTES


def t_program_audio_prepends_the_init_segment_for_fmp4():
    """End to end through the real pump: what reaches ffmpeg's stdin must start
    with ftyp/moov and continue at a moof box, never mid-mdat."""
    frag1 = _fragment(b"\x33" * 400)
    frag2 = _fragment(b"\x44" * 400)
    ring = m.FeedRing(1_000_000)
    ring.write(_INIT + frag1 + frag2)
    written = io.BytesIO()

    class _Stdin:
        def write(self, b): written.write(b)
        def flush(self): pass
        def close(self): pass

    class FakeRelay:
        feed_prebuffer_s = 0.0

    svc = m.ProgramAudioService(FakeRelay(), logging.getLogger("t576"))
    # Join mid-mdat, the way a live consumer does. proc=None ends the pump after
    # the first read, so the assertion sees exactly one join.
    svc._join_offset = lambda r, now: len(_INIT) + 20
    svc._feed_stdin(_Stdin(), ring, proc=None)
    out = written.getvalue()
    assert out == _INIT + frag2, (out[:40], len(out))


def t_program_audio_leaves_mpeg_ts_untouched():
    """The TS path must stay byte-identical: no prefix, no alignment, no delay."""
    ts = b"".join(b"\x47" + bytes([i % 251]) * 187 for i in range(20))
    ring = m.FeedRing(1_000_000)
    ring.write(ts)
    written = io.BytesIO()

    class _Stdin:
        def write(self, b): written.write(b)
        def flush(self): pass
        def close(self): pass

    class FakeRelay:
        feed_prebuffer_s = 0.0

    svc = m.ProgramAudioService(FakeRelay(), logging.getLogger("t576ts"))
    svc._join_offset = lambda r, now: 0
    svc._feed_stdin(_Stdin(), ring, proc=None)
    assert written.getvalue() == ts


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
