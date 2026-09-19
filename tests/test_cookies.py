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
    def __init__(self, write=True):
        self.calls, self.write = [], write

    def cookies_arg(self):
        return self.calls[-1][self.calls[-1].index("--cookies") + 1]

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.write:
            with open(cmd[cmd.index("--cookies") + 1], "w", encoding="utf-8") as fh:
                fh.write(BROWSER_EXPORT)
        return type("Proc", (), {"stderr": b"", "returncode": 0})()


def _cookie_names(path):
    with open(path, encoding="utf-8") as fh:
        return sorted(l.split("\t")[5] for l in fh.read().splitlines() if l.count("\t") == 6)


def _run_get_cookies(argv, fake=None):
    fake = fake or _FakeYtDlp()
    real_run, real_argv = m.subprocess.run, sys.argv
    m.subprocess.run, sys.argv = fake, ["get-cookies.py"] + argv
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            m.main()
    finally:
        m.subprocess.run, sys.argv = real_run, real_argv
    return out.getvalue(), fake


def t_get_cookies_keeps_only_youtube_domains():
    with tempfile.TemporaryDirectory() as d:
        said, fake = _run_get_cookies(["firefox", "--runtime-dir", d])
        assert _cookie_names(os.path.join(d, "yt-cookies.txt")) == ["SAPISID"]
        # The raw export went to a private dir, never onto the jar the relay reads.
        assert os.path.dirname(fake.cookies_arg()) != d, fake.cookies_arg()
        assert os.listdir(d) == ["yt-cookies.txt"], os.listdir(d)
        assert "dropped 3" in said and "logged-in session detected" in said, said


def t_get_cookies_keeps_only_twitch_domains():
    with tempfile.TemporaryDirectory() as d:
        said, _ = _run_get_cookies(["firefox", "--runtime-dir", d, "--platform", "twitch"])
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



def _old_jar(d):
    p = os.path.join(d, "yt-cookies.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("OLD JAR\n")
    return p


def _expect_exit(argv, fake=None):
    try:
        said, _ = _run_get_cookies(argv, fake)
    except SystemExit as exc:
        return str(exc.code)
    raise AssertionError(f"expected a failed export, got: {said}")


def t_get_cookies_fails_closed_when_the_filter_fails():
    real = m.cookie_jar.filter_jar
    m.cookie_jar.filter_jar = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as d:
            jar = _old_jar(d)
            code = _expect_exit(["firefox", "--runtime-dir", d])
            assert "previous jar is unchanged" in code, code
            with open(jar, encoding="utf-8") as fh:
                assert fh.read() == "OLD JAR\n"
            assert os.listdir(d) == ["yt-cookies.txt"], os.listdir(d)
    finally:
        m.cookie_jar.filter_jar = real


def t_get_cookies_fails_when_yt_dlp_writes_nothing():
    # An older jar in place must not turn a failed export into "OK".
    with tempfile.TemporaryDirectory() as d:
        jar = _old_jar(d)
        code = _expect_exit(["firefox", "--runtime-dir", d], _FakeYtDlp(write=False))
        assert code.startswith("FAILED to export from 'firefox'"), code
        with open(jar, encoding="utf-8") as fh:
            assert fh.read() == "OLD JAR\n"
        assert os.listdir(d) == ["yt-cookies.txt"], os.listdir(d)


def t_get_cookies_fails_cleanly_when_the_export_dir_cannot_be_made():
    with tempfile.TemporaryDirectory() as d:
        # Patched inside the test dir: TemporaryDirectory uses mkdtemp itself.
        real = m.cookie_jar.tempfile.mkdtemp
        m.cookie_jar.tempfile.mkdtemp = lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
        try:
            code = _expect_exit(["firefox", "--runtime-dir", d])
        finally:
            m.cookie_jar.tempfile.mkdtemp = real
        assert code.startswith("ERROR: cannot prepare the cookie export"), code


def _relay():
    rspec = importlib.util.spec_from_file_location(
        "racecast_feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    relay = importlib.util.module_from_spec(rspec); rspec.loader.exec_module(relay)
    return relay


def t_relay_export_cookies_fails_closed_when_the_filter_fails():
    relay = _relay()
    real_run, real_filter = relay.subprocess.run, relay.cookie_jar.filter_jar
    relay.subprocess.run = _FakeYtDlp()
    relay.cookie_jar.filter_jar = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as d:
            jar = _old_jar(d)
            assert relay.export_cookies("firefox", jar) is False
            with open(jar, encoding="utf-8") as fh:
                assert fh.read() == "OLD JAR\n"
            assert os.listdir(d) == ["yt-cookies.txt"], os.listdir(d)
    finally:
        relay.subprocess.run, relay.cookie_jar.filter_jar = real_run, real_filter


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
