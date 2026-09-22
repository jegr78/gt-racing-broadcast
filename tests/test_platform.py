# tests/test_platform.py
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "relay"))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "feeds", os.path.join(os.path.dirname(__file__), "..", "src", "relay", "racecast-feeds.py"))
feeds = importlib.util.module_from_spec(spec); spec.loader.exec_module(feeds)


def t_platform_of():
    assert feeds.platform_of("https://www.youtube.com/watch?v=abc") == "youtube"
    assert feeds.platform_of("https://youtu.be/abc") == "youtube"
    assert feeds.platform_of("https://www.twitch.tv/somechannel") == "twitch"
    assert feeds.platform_of("https://TWITCH.TV/Chan") == "twitch"      # case-insensitive
    assert feeds.platform_of("https://m.twitch.tv/chan") == "twitch"    # subdomain
    # a bare UC id, which channel_url turns into a youtube URL -> youtube
    assert feeds.platform_of("UC1234567890123456789012") == "youtube"
    assert feeds.platform_of("https://twitch.tv@evil.com/") == "youtube", \
        "the userinfo trick must not be seen as twitch"


def t_serve_cmd_youtube():
    cmd = feeds.streamlink_serve_cmd("https://hls.example/x.m3u8", 53001)
    assert "--twitch-low-latency" not in cmd
    assert cmd[-2:] == ["https://hls.example/x.m3u8", "best"]
    assert "--" in cmd and cmd.index("--") < cmd.index("https://hls.example/x.m3u8")


def t_serve_cmd_youtube_browser_ua():
    # streamlink re-fetches the yt-dlp-resolved manifest in a separate process, so it
    # must carry a browser User-Agent or YouTube 403s the bare re-fetch of a protected
    # live manifest. yt-dlp sends a browser UA on the resolve; streamlink must too. (#345)
    cmd = feeds.streamlink_serve_cmd("https://hls.example/x.m3u8", 53001)
    i = cmd.index("--http-header")
    assert cmd[i + 1].startswith("User-Agent=")
    assert "Mozilla/" in cmd[i + 1]
    assert i < cmd.index("--")                          # header is an option, before the URL


def t_serve_cmd_youtube_cookies():
    # the same cookies yt-dlp authenticated the resolve with must be handed to
    # streamlink's fetch, because unlisted and members streams bind the manifest to
    # the session
    cmd = feeds.streamlink_serve_cmd("https://hls.example/x.m3u8", 53001,
                                     cookies="/tmp/yt-cookies.txt")
    i = cmd.index("--http-cookies-file")
    assert cmd[i + 1] == "/tmp/yt-cookies.txt"
    assert i < cmd.index("--")                          # option before the URL
    # absent when there is no cookies file, as for a public stream
    cmd2 = feeds.streamlink_serve_cmd("https://hls.example/x.m3u8", 53001)
    assert "--http-cookies-file" not in cmd2


def t_serve_cmd_twitch_no_youtube_context():
    # the Twitch plugin resolves and fetches in-process, so it must not inherit the
    # YouTube browser-UA override or the yt cookies file
    cmd = feeds.streamlink_serve_cmd("https://www.twitch.tv/chan", 53002,
                                     platform="twitch", cookies="/tmp/yt-cookies.txt")
    assert "--http-cookies-file" not in cmd
    assert "--http-header" not in cmd


def t_serve_cmd_twitch():
    cmd = feeds.streamlink_serve_cmd("https://www.twitch.tv/chan", 53002, platform="twitch")
    assert "--twitch-low-latency" in cmd
    assert "--twitch-disable-ads" not in cmd            # deprecated; ads filtered automatically
    assert cmd[cmd.index("--hls-live-edge") + 1] == "2"  # tighter than the default 4
    assert cmd[-2:] == ["https://www.twitch.tv/chan", "best"]


def t_serve_cmd_twitch_token():
    cmd = feeds.streamlink_serve_cmd("https://www.twitch.tv/chan", 53002,
                                     platform="twitch", twitch_token="abc123")
    i = cmd.index("--twitch-api-header")
    assert cmd[i + 1] == "Authorization=OAuth abc123"
    assert i < cmd.index("--")                          # header is an option, before the URL


def t_ssai_markers():
    clean = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg0.ts\n#EXTINF:2.0,\nseg1.ts\n"
    assert feeds.manifest_has_ssai_markers(clean) is False
    cue = clean + "#EXT-X-CUE-OUT:30.0\n#EXTINF:2.0,\nad0.ts\n"
    assert feeds.manifest_has_ssai_markers(cue) is True
    daterange = clean + '#EXT-X-DATERANGE:ID="ad1",CLASS="twitch-stitched-ad",START-DATE="..."\n'
    assert feeds.manifest_has_ssai_markers(daterange) is True
    assert feeds.manifest_has_ssai_markers("") is False
    assert feeds.manifest_has_ssai_markers(None) is False


def t_cookies_for():
    d = tempfile.mkdtemp()
    # nothing present gives None for both
    assert feeds.cookies_for("youtube", d) is None
    assert feeds.cookies_for("twitch", d) is None
    assert feeds.cookies_for("youtube", None) is None
    # the legacy cookies.txt is still picked up for youtube
    legacy = os.path.join(d, "cookies.txt")
    with open(legacy, "w") as f: f.write("x")
    assert feeds.cookies_for("youtube", d) == legacy
    # yt-cookies.txt wins over the legacy name
    new = os.path.join(d, "yt-cookies.txt")
    with open(new, "w") as f: f.write("x")
    assert feeds.cookies_for("youtube", d) == new
    tw = os.path.join(d, "twitch-cookies.txt")
    with open(tw, "w") as f: f.write("x")
    assert feeds.cookies_for("twitch", d) == tw


def t_migrate_legacy():
    d = tempfile.mkdtemp()
    assert feeds.migrate_legacy_cookie(d).endswith("yt-cookies.txt"), \
        "no files: a no-op that returns the canonical path"
    # legacy present, new absent: renamed
    legacy = os.path.join(d, "cookies.txt")
    with open(legacy, "w") as f: f.write("x")
    p = feeds.migrate_legacy_cookie(d)
    assert p.endswith("yt-cookies.txt") and os.path.isfile(p) and not os.path.isfile(legacy)
    # both present: the legacy file is left alone and the new one wins
    with open(legacy, "w") as f: f.write("y")
    p2 = feeds.migrate_legacy_cookie(d)
    assert os.path.isfile(p2) and os.path.isfile(legacy)


def t_twitch_oauth():
    assert feeds.twitch_oauth_from_cookies(None) is None
    assert feeds.twitch_oauth_from_cookies("/no/such/file") is None
    d = tempfile.mkdtemp(); p = os.path.join(d, "twitch-cookies.txt")
    # Netscape format: domain \t flag \t path \t secure \t expiry \t name \t value
    with open(p, "w") as f:
        f.write(
            "# Netscape HTTP Cookie File\n"
            ".twitch.tv\tTRUE\t/\tTRUE\t0\tauth-token\tdeadbeefcafe0123\n"
            ".twitch.tv\tTRUE\t/\tTRUE\t0\tother\tnope\n")
    assert feeds.twitch_oauth_from_cookies(p) == "deadbeefcafe0123"
    # file without auth-token -> None
    with open(p, "w") as f:
        f.write(".twitch.tv\tTRUE\t/\tTRUE\t0\tother\tnope\n")
    assert feeds.twitch_oauth_from_cookies(p) is None


def _load_getcookies():
    p = os.path.join(os.path.dirname(__file__), "..", "src", "relay", "get-cookies.py")
    spec = importlib.util.spec_from_file_location("getck", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def t_cookie_target():
    gc = _load_getcookies()
    out_yt, url_yt = gc.cookie_target("youtube", "/run")
    # an exact URL match, not a substring check, so py/incomplete-url-substring-sanitization stays quiet
    assert out_yt.endswith("yt-cookies.txt")
    assert url_yt == "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    out_tw, url_tw = gc.cookie_target("twitch", "/run")
    assert out_tw.endswith("twitch-cookies.txt")
    assert url_tw == "https://www.twitch.tv"


def t_failure_hint_twitch_no_profile():
    gc = _load_getcookies()
    hint = gc.failure_hint("could not find firefox cookies database", "firefox", "twitch")
    assert "firefox" in hint.lower() and "installed" in hint.lower()


def t_failure_hint_twitch_generic_mentions_twitch():
    gc = _load_getcookies()
    hint = gc.failure_hint("something else", "firefox", "twitch")
    assert "Twitch" in hint and "logged in" in hint.lower()


def t_failure_hint_twitch_decrypt_suggests_twitch_command():
    gc = _load_getcookies()
    hint = gc.failure_hint("ERROR: Failed to decrypt with DPAPI", "chrome", "twitch")
    assert "Twitch" in hint
    assert "racecast cookies twitch firefox" in hint


def t_failure_hint_youtube_decrypt_keeps_original_command():
    gc = _load_getcookies()
    hint = gc.failure_hint("ERROR: Failed to decrypt with DPAPI", "chrome", "youtube")
    assert "YouTube" in hint
    assert "racecast cookies firefox" in hint
    assert "twitch" not in hint.lower()


def t_failure_hint_default_platform_is_youtube():
    # The two-arg call, as racecast-feeds._cookie_hint makes it, must still say YouTube.
    gc = _load_getcookies()
    hint = gc.failure_hint("", "brave")
    assert "YouTube" in hint


if __name__ == "__main__":
    t_platform_of(); t_serve_cmd_youtube(); t_serve_cmd_youtube_browser_ua(); t_serve_cmd_youtube_cookies(); t_serve_cmd_twitch_no_youtube_context(); t_serve_cmd_twitch(); t_serve_cmd_twitch_token(); t_ssai_markers(); t_cookies_for(); t_migrate_legacy(); t_twitch_oauth(); t_cookie_target()
    t_failure_hint_twitch_no_profile(); t_failure_hint_twitch_generic_mentions_twitch()
    t_failure_hint_twitch_decrypt_suggests_twitch_command(); t_failure_hint_youtube_decrypt_keeps_original_command()
    t_failure_hint_default_platform_is_youtube()
    print("ok")
