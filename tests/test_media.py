#!/usr/bin/env python3
"""Stdlib unit checks for get-media.py. Run: python3 tests/test_media.py"""
import importlib.util, inspect, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "getmedia", os.path.join(ROOT, "src", "relay", "get-media.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _load(name, rel):
    s = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


import sys as _sys
_sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))  # for the graphics load
media = m
graphics = _load("get_graphics", os.path.join("src", "relay", "get-graphics.py"))
brands = _load("get_brands", os.path.join("src", "relay", "get-brands.py"))

DRIVE = "https://drive.google.com/file/d/ABC123def456/view?usp=sharing"
YT = "https://www.youtube.com/watch?v=abc12345"

# A modern Google-Drive large-file interstitial: a <form> that GETs the
# drive.usercontent.google.com/download endpoint with hidden inputs. It carries no
# `confirm=<token>` query param; `confirm` is a hidden input valued "t". (#386)
FORM_INTERSTITIAL = (
    b"<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title>"
    b"</head><body>"
    b'<form id="download-form" action="https://drive.usercontent.google.com/download" '
    b'method="get">'
    b'<input type="hidden" name="id" value="FILEID123">'
    b'<input type="hidden" name="export" value="download">'
    b'<input type="hidden" name="authuser" value="0">'
    b'<input type="hidden" name="confirm" value="t">'
    b'<input type="hidden" name="uuid" value="abcd-uuid-1234">'
    b"</form></body></html>")


def t_urls_basic():
    rows = [["Intro Video", "https://youtu.be/AAA"],
            ["Outro Video", "https://youtu.be/BBB"]]
    assert m.media_urls_from_csv(rows) == {
        "intro": "https://youtu.be/AAA", "outro": "https://youtu.be/BBB"}, \
        m.media_urls_from_csv(rows)


def t_urls_label_case_and_gap():
    # The label match ignores case and spaces, and the URL is the next non-empty cell.
    rows = [["  intro video ", "", "https://youtu.be/AAA"]]
    assert m.media_urls_from_csv(rows) == {"intro": "https://youtu.be/AAA"}


def t_urls_label_without_value_omitted():
    rows = [["Intro Video", ""], ["Outro Video", "https://youtu.be/BBB"]]
    assert m.media_urls_from_csv(rows) == {"outro": "https://youtu.be/BBB"}


def t_urls_moved_columns():
    rows = [["foo", "bar", "Outro Video", "https://youtu.be/BBB"]]
    assert m.media_urls_from_csv(rows) == {"outro": "https://youtu.be/BBB"}


def t_urls_empty():
    assert m.media_urls_from_csv([]) == {}


def t_media_dir_repo():
    # Built with os.path.join because the separator differs on Windows.
    got = m.media_dir(os.path.join("/x", "src", "relay"))
    assert got == os.path.join("/x", "runtime", "media"), got


def t_media_dir_pkg():
    got = m.media_dir(os.path.join("/x/GT_Racecast_Package", "relay"))
    assert got == os.path.join("/x/GT_Racecast_Package", "media"), got


def t_resolve_priority_cli_then_env():
    cli = {"intro": "CLI", "outro": None}
    env = {"RACECAST_OUTRO_URL": "ENV"}
    csv_text = "Intro Video,SHEET_I\nOutro Video,SHEET_O\n"
    out = m.resolve_urls({"intro", "outro"}, cli, env, csv_text)
    assert out == {"intro": "CLI", "outro": "ENV"}, out


def t_resolve_sheet_fallback():
    out = m.resolve_urls({"intro"}, {"intro": None}, {}, "Intro Video,SHEET_I\n")
    assert out == {"intro": "SHEET_I"}, out


def t_resolve_missing_is_none():
    out = m.resolve_urls({"intro"}, {"intro": None}, {}, None)
    assert out == {"intro": None}, out


# Download argv separator and scheme guard.

def t_cookies_path_cli_override_wins():
    # In a frozen binary `here` points into the ephemeral PyInstaller bundle, so
    # the CLI's explicit --cookies path must win over the here-relative fallback.
    assert m.cookies_path("/real/runtime/yt-cookies.txt", "/bundle/src/relay") \
        == "/real/runtime/yt-cookies.txt"


def t_cookies_path_falls_back_to_runtime_jar():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        here = os.path.join(d, "src", "relay")          # media_dir(here) -> d/runtime/media
        rt = os.path.join(d, "runtime")
        os.makedirs(rt)
        open(os.path.join(rt, "yt-cookies.txt"), "w").close()
        assert m.cookies_path(None, here) == os.path.join(rt, "yt-cookies.txt")


def t_cookies_path_legacy_fallback():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        here = os.path.join(d, "src", "relay")
        rt = os.path.join(d, "runtime")
        os.makedirs(rt)
        open(os.path.join(rt, "cookies.txt"), "w").close()   # only legacy present
        assert m.cookies_path(None, here) == os.path.join(rt, "cookies.txt")


def t_download_cmd_separates_url():
    cmd = m.build_download_cmd("https://youtu.be/AAA", "/tmp/intro.mp4")
    assert cmd[-2:] == ["--", "https://youtu.be/AAA"], cmd
    assert "-o" in cmd and cmd[cmd.index("-o") + 1] == "/tmp/intro.mp4"
    assert "--cookies" not in cmd


def t_download_cmd_forces_overwrite():
    # yt-dlp skips an existing output file by default, which would freeze a stale
    # clip in place. --force-overwrites plus no partial resume keeps the on-disk
    # file matching the resolved URL.
    cmd = m.build_download_cmd("https://youtu.be/AAA", "/tmp/trailer.mp4")
    assert "--force-overwrites" in cmd, cmd
    assert "--no-continue" in cmd, cmd
    # The overwrite flags stay options, before the `--` URL separator.
    assert cmd.index("--force-overwrites") < cmd.index("--"), cmd


def t_download_cmd_cookies_before_separator():
    import tempfile, os as _os
    fd, ck = tempfile.mkstemp(); _os.close(fd)
    try:
        cmd = m.build_download_cmd("https://youtu.be/AAA", "/tmp/o.mp4", ck)
        assert cmd[-2:] == ["--", "https://youtu.be/AAA"], cmd
        assert cmd.index("--cookies") < cmd.index("--")    # cookies stay an option
    finally:
        _os.unlink(ck)


def t_download_rejects_non_http_url():
    for bad in ("--exec=evil", "file:///etc/passwd", "ftp://x/y", "-o/tmp/x"):
        raised = False
        try:
            m.download(bad, "/tmp/out.mp4")
        except ValueError:
            raised = True
        assert raised, bad


# The transient-403 retry loop. (#344)

class _Runner:
    """Fake subprocess.run: each call pops the next item from `results`. An
    Exception is raised, anything else is returned as a success."""
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, cmd, check=True, timeout=None, env=None):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _Sleeper:
    def __init__(self):
        self.calls = []

    def __call__(self, delay):
        self.calls.append(delay)


def t_run_download_first_try_no_retry():
    run, sl = _Runner(["OK"]), _Sleeper()
    assert m.run_download(["yt-dlp"], runner=run, sleeper=sl) == "OK"
    assert run.calls == 1 and sl.calls == [], (run.calls, sl.calls)


def t_run_download_retries_then_succeeds():
    err = subprocess.CalledProcessError(1, "yt-dlp")
    run, sl = _Runner([err, "OK"]), _Sleeper()
    assert m.run_download(["yt-dlp"], runner=run, sleeper=sl) == "OK"
    # One failed attempt means one retry and one backoff sleep.
    assert run.calls == 2 and len(sl.calls) == 1, (run.calls, sl.calls)


def t_run_download_gives_up_after_attempts():
    err = subprocess.CalledProcessError(1, "yt-dlp")
    run, sl = _Runner([err, err, err]), _Sleeper()
    raised = False
    try:
        m.run_download(["yt-dlp"], attempts=3, runner=run, sleeper=sl)
    except subprocess.CalledProcessError:
        raised = True
    # All attempts are used, with a sleep between each, and the error re-raised.
    assert raised and run.calls == 3 and len(sl.calls) == 2, (run.calls, sl.calls)


def t_run_download_does_not_retry_missing_ytdlp():
    run, sl = _Runner([FileNotFoundError()]), _Sleeper()
    raised = False
    try:
        m.run_download(["yt-dlp"], runner=run, sleeper=sl)
    except FileNotFoundError:
        raised = True
    assert raised and run.calls == 1 and sl.calls == [], (run.calls, sl.calls)


def t_run_download_does_not_retry_timeout():
    run, sl = _Runner([subprocess.TimeoutExpired("yt-dlp", 600)]), _Sleeper()
    raised = False
    try:
        m.run_download(["yt-dlp"], runner=run, sleeper=sl)
    except subprocess.TimeoutExpired:
        raised = True
    assert raised and run.calls == 1 and sl.calls == [], (run.calls, sl.calls)


# Intermission-music helpers.

def t_music_url_from_csv_picks_value():
    rows = [["Overlay", "https://drive.google.com/file/d/x/view"],
            ["Intermission Music", DRIVE],
            ["Intro Video", YT]]
    assert media.music_url_from_csv(rows) == DRIVE


def t_music_download_kind():
    assert media.music_download_kind(DRIVE) == "drive"
    assert media.music_download_kind(YT) == "ytdlp"
    assert media.music_download_kind("file:///etc/passwd") == "invalid"
    assert media.music_download_kind("ftp://x/y") == "invalid"


def t_build_music_cmd_is_audio_extract_and_guarded():
    argv = media.build_music_cmd(YT, "/out/intermission.mp3")
    assert argv[0] == "yt-dlp"
    assert "-x" in argv
    i = argv.index("--audio-format"); assert argv[i + 1] == "mp3"
    assert "--" in argv and argv.index("--") < argv.index(YT)   # flag-injection guard
    assert argv[-1] == YT


def t_build_music_cmd_output_stem_is_intermission():
    argv = media.build_music_cmd(YT, "/out/intermission.mp3")
    o = argv.index("-o")
    assert os.path.basename(argv[o + 1]).startswith("intermission.")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def t_reset_unlinked_media_overwrites_stale_clip():
    # A stale intro.mp4 must be replaced by the neutral placeholder once the Sheet
    # stops linking it, while a clip nobody asked for is left alone. (#387)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "intro.mp4"), "wb") as fh:
            fh.write(b"STALE-INTRO")
        written = media.reset_unlinked_media(tmp, {"intro"}, want_music=False)
        assert written == ["intro.mp4"]
        neutral = _read(media.placeholders.media_placeholder_for("intro.mp4"))
        assert _read(os.path.join(tmp, "intro.mp4")) == neutral
        assert not os.path.exists(os.path.join(tmp, "outro.mp4"))


def t_reset_unlinked_media_music():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "intermission.mp3"), "wb") as fh:
            fh.write(b"STALE-MUSIC")
        written = media.reset_unlinked_media(tmp, set(), want_music=True)
        assert written == ["intermission.mp3"]
        loop = _read(media.placeholders.media_placeholder_for("intermission.mp3"))
        assert _read(os.path.join(tmp, "intermission.mp3")) == loop


def t_drive_helpers_match_get_graphics():
    for fn in ("is_drive_url", "drive_id", "to_download_url", "drive_confirm_url"):
        assert inspect.getsource(getattr(media, fn)) == inspect.getsource(getattr(graphics, fn)), \
            f"{fn} drifted between get-media and get-graphics"


def t_drive_helpers_match_get_brands():
    for fn in ("is_drive_url", "drive_id", "to_download_url", "drive_confirm_url"):
        assert inspect.getsource(getattr(media, fn)) == inspect.getsource(getattr(brands, fn)), \
            f"{fn} drifted between get-media and get-brands"


def t_drive_confirm_url_form_interstitial():
    """The modern <form> interstitial resolves to a usercontent GET with every
    hidden input carried through. (#386)"""
    from urllib.parse import urlparse, parse_qs
    url = media.to_download_url("FILEID123")
    got = media.drive_confirm_url(url, FORM_INTERSTITIAL)
    p = urlparse(got)
    assert (p.scheme, p.netloc, p.path) == (
        "https", "drive.usercontent.google.com", "/download"), got
    q = parse_qs(p.query)
    assert q["id"] == ["FILEID123"]
    assert q["export"] == ["download"]
    assert q["confirm"] == ["t"]
    assert q["uuid"] == ["abcd-uuid-1234"]


def t_drive_confirm_url_legacy_token():
    """The legacy inline `confirm=<token>` link still resolves."""
    url = media.to_download_url("XYZ")
    body = b'<a href="/uc?export=download&confirm=AbC_9-tok&id=XYZ">Download</a>'
    assert media.drive_confirm_url(url, body) == url + "&confirm=AbC_9-tok"


def t_drive_confirm_url_none_when_neither():
    assert media.drive_confirm_url("u", b"<html><body>nothing here</body></html>") is None


def t_drive_confirm_url_input_attr_order_independent():
    """A value-before-name input still parses, so attribute order does not matter."""
    from urllib.parse import urlparse, parse_qs
    body = (
        b'<form action="https://drive.usercontent.google.com/download">'
        b'<input value="ID9" name="id" type="hidden">'
        b'<input type="hidden" name="confirm" value="t"></form>')
    got = media.drive_confirm_url("u", body)
    q = parse_qs(urlparse(got).query)
    assert q["id"] == ["ID9"] and q["confirm"] == ["t"]


def t_urls_trailer_label():
    rows = [["Trailer Video", "https://youtu.be/TTT"]]
    assert m.media_urls_from_csv(rows) == {"trailer": "https://youtu.be/TTT"}, \
        m.media_urls_from_csv(rows)


def t_urls_all_three_media_labels():
    rows = [["Intro Video", "https://youtu.be/AAA"],
            ["Outro Video", "https://youtu.be/BBB"],
            ["Trailer Video", "https://youtu.be/TTT"]]
    assert m.media_urls_from_csv(rows) == {
        "intro": "https://youtu.be/AAA", "outro": "https://youtu.be/BBB",
        "trailer": "https://youtu.be/TTT"}


def t_resolve_trailer_priority_cli_then_env():
    cli = {"trailer": "CLI"}
    env = {"RACECAST_TRAILER_URL": "ENV"}
    assert m.resolve_urls({"trailer"}, cli, env, None) == {"trailer": "CLI"}
    assert m.resolve_urls({"trailer"}, {"trailer": None}, env, None) == {"trailer": "ENV"}


def t_graphics_skip_set_includes_trailer():
    # get-graphics must skip the Trailer row so it is not downloaded as a PNG.
    assert "trailer video" in graphics.MEDIA_LABELS


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("ALL PASS")
