#!/usr/bin/env python3
"""Stdlib unit checks for the relay's automatic cookie refresh (#1092).
Run: python3 tests/test_cookie_refresh.py"""
import importlib.util, os, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cookie_jar = m.cookie_jar

YT = "https://youtu.be/abc"
TW = "https://www.twitch.tv/somebody"


def t_browser_comes_from_the_machine_env_and_is_allowlisted():
    pick = m.cookie_refresh_browser
    assert pick({}) is None, "unset = no automatic refresh"
    assert pick({"RACECAST_COOKIES_BROWSER": ""}) is None
    assert pick({"RACECAST_COOKIES_BROWSER": " Firefox "}) == "firefox"
    assert pick({"RACECAST_COOKIES_BROWSER": "edge"}) == "edge"
    # The value reaches yt-dlp's argv: a profile path or an option is refused.
    assert pick({"RACECAST_COOKIES_BROWSER": "firefox:/tmp/x"}) is None
    assert pick({"RACECAST_COOKIES_BROWSER": "--exec"}) is None


def t_refresh_is_due_only_for_a_needed_jar_past_the_age():
    due = m.cookie_refresh_due
    assert due(2.0, needed=True) is True
    assert due(0.5, needed=True) is False
    assert due(None, needed=True) is True, "an unstamped jar has an unknown age"
    assert due(48.0, needed=False) is False, "nobody reads it: leave it alone"


def t_twitch_cookies_needed_only_for_twitch_sources():
    need = m.twitch_cookies_needed
    assert need([YT, "local:", ""]) is False
    assert need([YT, TW]) is True


class _Relay:
    def __init__(self, cookies, items):
        self.cookies = cookies
        src = types.SimpleNamespace(get=lambda: list(items))
        self.race_source, self.qual_source, self.pov_source = src, None, None

    def cookies_needed(self):
        return m.Relay.cookies_needed(self)

    def cookie_feed_urls(self):
        return m.Relay.cookie_feed_urls(self)


def _jar(td, name, age_h, now):
    path = os.path.join(td, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n")
    cookie_jar.record_export(path, now=now - age_h * 3600)
    return path


def _refresher(relay, td, now, results):
    calls = []

    def export(browser, out, platform):
        calls.append((browser, os.path.basename(out), platform))
        return results.get(platform, True)
    clock = {"t": now}
    r = m.CookieRefresher(relay, "firefox", td, export=export, clock=lambda: clock["t"])
    return r, calls, clock


def t_refresher_re_exports_old_jars_that_a_feed_uses():
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as td:
        yt = _jar(td, "yt-cookies.txt", 3, now)
        _jar(td, "twitch-cookies.txt", 3, now)
        r, calls, _ = _refresher(_Relay(yt, [YT, TW]), td, now, {})
        r.tick()
        assert calls == [("firefox", "yt-cookies.txt", "youtube"),
                         ("firefox", "twitch-cookies.txt", "twitch")], calls


def t_refresher_leaves_fresh_unused_or_absent_jars_alone():
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as td:
        yt = _jar(td, "yt-cookies.txt", 0.2, now)          # fresh
        _jar(td, "twitch-cookies.txt", 30, now)            # old, but no Twitch feed
        r, calls, _ = _refresher(_Relay(yt, [YT, "local:"]), td, now, {})
        r.tick()
        assert calls == [], calls
    with tempfile.TemporaryDirectory() as td:
        # No jar at all: the relay started cookie-less and its feeds never read one,
        # so creating a jar now would change nothing; the producer opts in by exporting.
        r, calls, _ = _refresher(_Relay(None, [YT, TW]), td, now, {})
        r.tick()
        assert calls == [], calls


def t_a_failed_export_backs_off_instead_of_retrying_every_check():
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as td:
        yt = _jar(td, "yt-cookies.txt", 3, now)
        r, calls, clock = _refresher(_Relay(yt, [YT]), td, now, {"youtube": False})
        r.tick()
        clock["t"] += m.COOKIE_REFRESH_CHECK_S
        r.tick()
        assert len(calls) == 1, "one failure, then wait"
        clock["t"] = now + m.COOKIE_REFRESH_RETRY_S + 1
        r.tick()
        assert len(calls) == 2, calls


def t_a_broken_export_never_kills_the_refresher():
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as td:
        yt = _jar(td, "yt-cookies.txt", 3, now)
        relay = _Relay(yt, [YT])

        def boom(browser, out, platform):
            raise RuntimeError("yt-dlp exploded")
        r = m.CookieRefresher(relay, "firefox", td, export=boom, clock=lambda: now)
        r.tick()   # must not raise


def t_export_urls_match_get_cookies():
    # The relay and `racecast cookies` must open the same page per platform.
    gspec = importlib.util.spec_from_file_location(
        "get_cookies", os.path.join(ROOT, "src", "relay", "get-cookies.py"))
    gc = importlib.util.module_from_spec(gspec); gspec.loader.exec_module(gc)
    for platform, url in m.COOKIE_EXPORT_URLS.items():
        assert gc.cookie_target(platform, "rt")[1] == url, platform


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
