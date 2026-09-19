#!/usr/bin/env python3
"""Stdlib checks for get-cookies helpers. Run: python3 tests/test_cookies.py"""
import contextlib, importlib.util, io, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "get_cookies", os.path.join(ROOT, "src", "relay", "get-cookies.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_hint_locked_db_says_close_browser():
    # Chromium browsers lock their cookie DB while running (yt-dlp #7271).
    err = "ERROR: Could not copy Chrome cookie database. See ... for more info"
    hint = m.failure_hint(err, "chrome")
    assert "close" in hint.lower() and "chrome" in hint.lower()


def t_hint_no_profile_says_installed():
    err = 'ERROR: could not find chrome cookies database in "..."'
    assert "installed" in m.failure_hint(err, "chrome").lower()


def t_hint_decrypt_suggests_firefox():
    err = "ERROR: Failed to decrypt with DPAPI"
    hint = m.failure_hint(err, "chrome")
    assert "firefox" in hint.lower()


def t_hint_default_is_generic():
    for err in ("", None, "ERROR: something else entirely"):
        hint = m.failure_hint(err, "brave")
        assert "brave" in hint.lower() and "logged in" in hint.lower()


def t_relay_cookie_hint_delegates_to_get_cookies():
    # The relay's export_cookies() reuses failure_hint from the sibling
    # get-cookies.py — same hints in both flows, one source of truth.
    rspec = importlib.util.spec_from_file_location(
        "racecast_feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    relay = importlib.util.module_from_spec(rspec); rspec.loader.exec_module(relay)
    err = "ERROR: Could not copy Chrome cookie database."
    assert relay._cookie_hint(err, "chrome") == m.failure_hint(err, "chrome")


def t_default_runtime_dir_repo_and_dist():
    repo = os.path.join("x", "repo", "src", "relay")
    assert m.default_runtime_dir(repo) == os.path.join("x", "repo", "runtime")
    dist = os.path.join("x", "pkg", "relay")
    assert m.default_runtime_dir(dist) == dist


# What yt-dlp --cookies-from-browser writes: the whole browser profile (#616).
BROWSER_EXPORT = ("# Netscape HTTP Cookie File\n"
                  ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tyt\n"
                  ".google.com\tTRUE\t/\tTRUE\t0\tSID\tgoogle\n"
                  ".github.com\tTRUE\t/\tTRUE\t0\tlogged_in\tgithub\n"
                  ".twitch.tv\tTRUE\t/\tTRUE\t0\tauth-token\ttwitch\n")


class _FakeYtDlp:
    """Stands in for subprocess.run: writes BROWSER_EXPORT to the --cookies path."""
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        with open(cmd[cmd.index("--cookies") + 1], "w", encoding="utf-8") as fh:
            fh.write(BROWSER_EXPORT)
        return type("Proc", (), {"stderr": b"", "returncode": 0})()


def _cookie_names(path):
    with open(path, encoding="utf-8") as fh:
        return sorted(l.split("\t")[5] for l in fh.read().splitlines() if l.count("\t") == 6)


def _run_get_cookies(argv):
    fake, real_run, real_argv = _FakeYtDlp(), m.subprocess.run, sys.argv
    m.subprocess.run, sys.argv = fake, ["get-cookies.py"] + argv
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            m.main()
    finally:
        m.subprocess.run, sys.argv = real_run, real_argv
    return out.getvalue()


def t_get_cookies_keeps_only_youtube_domains():
    with tempfile.TemporaryDirectory() as d:
        said = _run_get_cookies(["firefox", "--runtime-dir", d])
        assert _cookie_names(os.path.join(d, "yt-cookies.txt")) == ["SAPISID"]
        assert "dropped 3" in said and "logged-in session detected" in said, said


def t_get_cookies_keeps_only_twitch_domains():
    with tempfile.TemporaryDirectory() as d:
        said = _run_get_cookies(["firefox", "--runtime-dir", d, "--platform", "twitch"])
        assert _cookie_names(os.path.join(d, "twitch-cookies.txt")) == ["auth-token"]
        assert "dropped 3" in said and "logged-in session detected" in said, said


def t_relay_export_cookies_keeps_only_youtube_domains():
    rspec = importlib.util.spec_from_file_location(
        "racecast_feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    relay = importlib.util.module_from_spec(rspec); rspec.loader.exec_module(relay)
    real_run = relay.subprocess.run
    relay.subprocess.run = _FakeYtDlp()
    try:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "yt-cookies.txt")
            assert relay.export_cookies("firefox", out) is True
            assert _cookie_names(out) == ["SAPISID"]
    finally:
        relay.subprocess.run = real_run


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
