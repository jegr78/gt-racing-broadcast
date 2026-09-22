#!/usr/bin/env python3
"""Stdlib unit checks for the read-only broadcast-chat reader (issue #294).

Run: python3 tests/test_broadcast_chat.py

Covers the pure pieces only, with no network: the message sanitizer, the
Innertube JSON parsers for the page bootstrap and the get_live_chat continuation
response, runs->text rendering, the `Channel` tab CSV parser, the live-set diff
that drives the producer-handover start and stop of per-stream readers, and the
URL and body builders. The relay owns the yt-dlp subprocess and the HTTP fetch.
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


bc = _load("broadcast_chat", ("src", "scripts", "broadcast_chat.py"))


# sanitize_message

def t_sanitize_basic():
    m = bc.sanitize_message({"ts": 100.0, "user": "Bob", "text": "hi"}, source="vid1")
    assert m == {"ts": 100.0, "user": "Bob", "text": "hi", "source": "vid1"}


def t_sanitize_caps_name_and_text():
    m = bc.sanitize_message({"ts": 1.0, "user": "x" * 200, "text": "y" * 900})
    assert len(m["user"]) == bc.MAX_NAME
    assert len(m["text"]) == bc.MAX_TEXT


def t_sanitize_strips_control_chars_keeps_space():
    m = bc.sanitize_message({"ts": 1.0, "user": "a\x00b", "text": "li\x07ne\tone"})
    assert m["user"] == "ab"
    assert m["text"] == "line one"   # tab folded to a space, BEL dropped


def t_sanitize_default_name():
    m = bc.sanitize_message({"ts": 1.0, "user": "   ", "text": "hi"})
    assert m["user"] == bc.DEFAULT_NAME


def t_sanitize_rejects_non_numeric_ts():
    assert bc.sanitize_message({"ts": "nope", "user": "B", "text": "hi"}) is None


def t_sanitize_rejects_empty_text():
    assert bc.sanitize_message({"ts": 1.0, "user": "B", "text": "   "}) is None


def t_sanitize_bool_ts_rejected():
    assert bc.sanitize_message({"ts": True, "user": "B", "text": "hi"}) is None


# runs_to_text

def t_runs_to_text_joins_text_runs():
    msg = {"runs": [{"text": "hello "}, {"text": "world"}]}
    assert bc.runs_to_text(msg) == "hello world"


def t_runs_to_text_emoji_uses_shortcut():
    # With no emojiId glyph the shortcut is the fallback.
    msg = {"runs": [{"text": "gg "},
                    {"emoji": {"shortcuts": [":smile:"], "isCustomEmoji": False}}]}
    assert bc.runs_to_text(msg) == "gg :smile:"


def t_runs_to_text_standard_emoji_uses_glyph():
    # A standard emoji carries its Unicode glyph in emojiId, so render the glyph
    # rather than the :shortcut: text.
    msg = {"runs": [{"text": "low stigs "},
                    {"emoji": {"emojiId": "\U0001f605",
                               "shortcuts": [":grinning_face_with_sweat:"],
                               "isCustomEmoji": False}}]}
    assert bc.runs_to_text(msg) == "low stigs \U0001f605"


def t_runs_to_text_custom_emoji_keeps_shortcut():
    # A custom channel emote has no Unicode glyph, since emojiId is an internal
    # id, so the :shortcut: text stays.
    msg = {"runs": [{"emoji": {"emojiId": "UCabc123/deadbeef",
                               "shortcuts": [":pog:"],
                               "isCustomEmoji": True}}]}
    assert bc.runs_to_text(msg) == ":pog:"


def t_runs_to_text_simpletext_fallback():
    assert bc.runs_to_text({"simpleText": "plain"}) == "plain"


def t_runs_to_text_empty():
    assert bc.runs_to_text({}) == ""
    assert bc.runs_to_text(None) == ""


# parse_chat_action

def t_parse_chat_action_text_message():
    action = {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": "abc",
        "authorName": {"simpleText": "Alice"},
        "message": {"runs": [{"text": "let's go"}]},
        "timestampUsec": "1700000000000000",
    }}}}
    m = bc.parse_chat_action(action)
    assert m["id"] == "abc"
    assert m["user"] == "Alice"
    assert m["text"] == "let's go"
    assert m["ts"] == 1700000000.0   # usec -> sec


def t_parse_chat_action_paid_message_includes_amount():
    action = {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
        "id": "p1",
        "authorName": {"simpleText": "Gen"},
        "purchaseAmountText": {"simpleText": "$5.00"},
        "message": {"runs": [{"text": "great race"}]},
        "timestampUsec": "1700000001000000",
    }}}}
    m = bc.parse_chat_action(action)
    assert m["user"] == "Gen"
    assert "$5.00" in m["text"]
    assert "great race" in m["text"]


def t_parse_chat_action_ignores_non_chat():
    assert bc.parse_chat_action({"addBannerToLiveChatCommand": {}}) is None
    assert bc.parse_chat_action({"addChatItemAction": {"item": {
        "liveChatViewerEngagementMessageRenderer": {}}}}) is None


def t_parse_chat_action_paid_message_no_text_ok():
    action = {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
        "id": "p2", "authorName": {"simpleText": "Gen"},
        "purchaseAmountText": {"simpleText": "$2.00"},
        "timestampUsec": "1700000002000000",
    }}}}
    m = bc.parse_chat_action(action)
    assert "$2.00" in m["text"]


# parse_live_chat (the get_live_chat POST response)

def _live_chat_payload(actions, continuation="CONT2", timeout=5000, kind="invalidationContinuationData"):
    return {"continuationContents": {"liveChatContinuation": {
        "continuations": [{kind: {"timeoutMs": timeout, "continuation": continuation}}],
        "actions": actions,
    }}}


def t_parse_live_chat_messages_and_continuation():
    actions = [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": "m1", "authorName": {"simpleText": "Al"},
        "message": {"runs": [{"text": "hi"}]},
        "timestampUsec": "1700000000000000"}}}}]
    out = bc.parse_live_chat(_live_chat_payload(actions))
    assert out["continuation"] == "CONT2"
    assert out["timeout_ms"] == 5000
    assert len(out["messages"]) == 1
    assert out["messages"][0]["text"] == "hi"


def t_parse_live_chat_handles_timed_continuation():
    out = bc.parse_live_chat(_live_chat_payload([], continuation="C9", kind="timedContinuationData"))
    assert out["continuation"] == "C9"


def t_parse_live_chat_handles_reload_continuation():
    out = bc.parse_live_chat(_live_chat_payload([], continuation="C7", kind="reloadContinuationData"))
    assert out["continuation"] == "C7"


def t_parse_live_chat_empty_actions():
    out = bc.parse_live_chat(_live_chat_payload([]))
    assert out["messages"] == []
    assert out["continuation"] == "CONT2"


def t_parse_live_chat_missing_contents_is_empty():
    out = bc.parse_live_chat({"responseContext": {}})
    assert out["messages"] == []
    assert out["continuation"] is None


def t_parse_live_chat_tolerates_garbage():
    out = bc.parse_live_chat(None)
    assert out["messages"] == []
    assert out["continuation"] is None


# classify_live_chat_poll: a transient failure is not an end. (#294)

def t_classify_none_is_transient():
    # A failed POST returns None and must count as transient, never as ended: an
    # ended verdict freezes the mirror for the rest of the stream. (#294)
    status, parsed = bc.classify_live_chat_poll(None)
    assert status == bc.POLL_TRANSIENT
    assert parsed["continuation"] is None
    assert parsed["messages"] == []


def t_classify_ok_when_continuation_present():
    actions = [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": "m1", "authorName": {"simpleText": "Al"},
        "message": {"runs": [{"text": "hi"}]},
        "timestampUsec": "1700000000000000"}}}}]
    status, parsed = bc.classify_live_chat_poll(_live_chat_payload(actions))
    assert status == bc.POLL_OK
    assert parsed["continuation"] == "CONT2"
    assert parsed["messages"][0]["text"] == "hi"


def t_classify_ended_when_wellformed_without_continuation():
    # A real HTTP 200 dict with no next continuation means the live chat closed,
    # so the reader may tombstone it.
    payload = {"continuationContents": {"liveChatContinuation": {"actions": []}}}
    status, parsed = bc.classify_live_chat_poll(payload)
    assert status == bc.POLL_ENDED
    assert parsed["continuation"] is None


def t_classify_distinguishes_transient_from_ended():
    # A None and a well-formed but ended response are different verdicts.
    ended = {"continuationContents": {"liveChatContinuation": {"actions": []}}}
    assert bc.classify_live_chat_poll(None)[0] != bc.classify_live_chat_poll(ended)[0]


# parse_bootstrap (the live_chat page HTML)

SAMPLE_PAGE = (
    'junk before <script>var x="INNERTUBE_API_KEY":"AIzaTESTKEY123";'
    '"INNERTUBE_CONTEXT_CLIENT_VERSION":"2.20260101.00.00";'
    'window["ytInitialData"] = {"contents":{"liveChatRenderer":{"continuations":'
    '[{"reloadContinuationData":{"continuation":"INITCONT=="}}]}}};'
    '</script> junk after'
)


def t_parse_bootstrap_extracts_fields():
    bs = bc.parse_bootstrap(SAMPLE_PAGE)
    assert bs["api_key"] == "AIzaTESTKEY123"
    assert bs["client_version"] == "2.20260101.00.00"
    assert bs["continuation"] == "INITCONT=="


def t_parse_bootstrap_missing_returns_none_fields():
    bs = bc.parse_bootstrap("nothing useful here")
    assert bs["api_key"] is None
    assert bs["continuation"] is None


# build_get_live_chat_body

def t_build_body_shape():
    body = bc.build_get_live_chat_body("CONTX", "2.20260101.00.00")
    assert body["continuation"] == "CONTX"
    assert body["context"]["client"]["clientVersion"] == "2.20260101.00.00"
    assert body["context"]["client"]["clientName"] == "WEB"
    # The body must be JSON-serialisable.
    json.dumps(body)


# URL builders

def t_channel_live_url_from_id():
    assert bc.channel_live_url("UC123") == "https://www.youtube.com/channel/UC123/live"


def t_channel_live_url_passthrough_url():
    u = "https://www.youtube.com/@league"
    assert bc.channel_live_url(u) == u + "/live"


def t_channel_live_url_passthrough_live_url():
    u = "https://www.youtube.com/@league/live"
    assert bc.channel_live_url(u) == u


def t_channel_streams_url_from_id():
    assert bc.channel_streams_url("UC123") == "https://www.youtube.com/channel/UC123/streams"


def t_live_chat_page_url():
    assert "v=vid9" in bc.live_chat_page_url("vid9")


def t_api_url_includes_key():
    assert "key=AIzaX" in bc.get_live_chat_api_url("AIzaX")


# compose targets (popup)

def t_youtube_video_id_valid():
    assert bc.youtube_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def t_youtube_video_id_rejects_wrong_length():
    assert bc.youtube_video_id("short") is None
    assert bc.youtube_video_id("a" * 12) is None


def t_youtube_video_id_rejects_illegal_chars_and_nonstr():
    assert bc.youtube_video_id("abc/def?xss") is None
    assert bc.youtube_video_id(None) is None


def t_twitch_popout_chat_url():
    assert bc.twitch_popout_chat_url("gtmaster") == \
        "https://www.twitch.tv/popout/gtmaster/chat"


def t_primary_chat_target_youtube_first():
    t = bc.primary_chat_target(["dQw4w9WgXcQ", "twitch:gtmaster"])
    assert t["platform"] == "youtube"
    assert "v=dQw4w9WgXcQ" in t["url"]


def t_primary_chat_target_twitch():
    assert bc.primary_chat_target(["twitch:gtmaster"]) == {
        "platform": "twitch",
        "url": "https://www.twitch.tv/popout/gtmaster/chat"}


def t_primary_chat_target_empty_is_none():
    assert bc.primary_chat_target([]) is None
    assert bc.primary_chat_target(None) is None


def t_primary_chat_target_skips_invalid_then_picks_next():
    # A first key that is neither an 11-char videoId nor twitch: is skipped.
    t = bc.primary_chat_target(["bad/key", "twitch:gtmaster"])
    assert t["platform"] == "twitch"


# live_set_diff (producer handover)

def t_live_set_diff_start_and_stop():
    to_start, to_stop = bc.live_set_diff({"A"}, {"A", "B"})
    assert to_start == {"B"}
    assert to_stop == set()


def t_live_set_diff_handover_overlap_then_drop():
    # A is on air, B comes up and starts, then A ends and stops.
    to_start, _ = bc.live_set_diff({"A"}, {"A", "B"})
    assert to_start == {"B"}
    _, to_stop = bc.live_set_diff({"A", "B"}, {"B"})
    assert to_stop == {"A"}


def t_live_set_diff_no_change():
    assert bc.live_set_diff({"A"}, {"A"}) == (set(), set())


# parse_channel_tab

def t_parse_channel_tab_header_mode():
    csv_text = "Platform,Channel\nyoutube,https://www.youtube.com/@league\n"
    rows = bc.parse_channel_tab(csv_text)
    assert rows == [("youtube", "https://www.youtube.com/@league")]


def t_parse_channel_tab_infers_platform_from_url():
    csv_text = "Channel\nhttps://www.twitch.tv/somecaster\n"
    rows = bc.parse_channel_tab(csv_text)
    assert rows == [("twitch", "https://www.twitch.tv/somecaster")]


def t_parse_channel_tab_infers_youtube_from_url():
    csv_text = "Channel\nhttps://www.youtube.com/@league/live\n"
    rows = bc.parse_channel_tab(csv_text)
    assert rows == [("youtube", "https://www.youtube.com/@league/live")]


def t_parse_channel_tab_skips_blank():
    csv_text = "Platform,Channel\nyoutube,\n,\nyoutube,UC123\n"
    rows = bc.parse_channel_tab(csv_text)
    assert rows == [("youtube", "UC123")]


def t_parse_channel_tab_empty():
    assert bc.parse_channel_tab("") == []
    assert bc.parse_channel_tab("Platform,Channel\n") == []


# twitch_login (Phase 2)

def t_twitch_login_from_url():
    assert bc.twitch_login("https://www.twitch.tv/SomeCaster") == "somecaster"


def t_twitch_login_from_url_trailing_slash():
    assert bc.twitch_login("https://www.twitch.tv/SomeCaster/") == "somecaster"


def t_twitch_login_no_scheme():
    assert bc.twitch_login("twitch.tv/SomeCaster") == "somecaster"


def t_twitch_login_bare_name():
    assert bc.twitch_login("SomeCaster") == "somecaster"


def t_twitch_login_strips_at():
    assert bc.twitch_login("@SomeCaster") == "somecaster"


def t_twitch_login_rejects_invalid():
    assert bc.twitch_login("bad name!") is None
    assert bc.twitch_login("") is None
    assert bc.twitch_login("https://www.twitch.tv/") is None


def t_twitch_login_rejects_crlf_injection():
    # A channel value must never inject IRC commands into the stream.
    assert bc.twitch_login("foo\r\nJOIN #evil") is None


def t_twitch_login_rejects_too_long():
    assert bc.twitch_login("a" * 26) is None


# parse_twitch_privmsg (Phase 2)

def t_parse_twitch_privmsg_tagged():
    line = ("@badge-info=;color=#1E90FF;display-name=CoolViewer;emotes=;id=abc-123;"
            "tmi-sent-ts=1700000000000;turbo=0;user-id=1 "
            ":coolviewer!coolviewer@coolviewer.tmi.twitch.tv PRIVMSG #somechannel :Hello chat!")
    m = bc.parse_twitch_privmsg(line)
    assert m["id"] == "abc-123"
    assert m["user"] == "CoolViewer"
    assert m["text"] == "Hello chat!"
    assert m["ts"] == 1700000000.0


def t_parse_twitch_privmsg_text_with_colons():
    line = ("@display-name=Bob;tmi-sent-ts=1700000000000 "
            ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :it's 3:30 — go!")
    assert bc.parse_twitch_privmsg(line)["text"] == "it's 3:30 — go!"


def t_parse_twitch_privmsg_no_tags_uses_prefix_nick():
    line = ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :hi there"
    m = bc.parse_twitch_privmsg(line)
    assert m["user"] == "bob"
    assert m["text"] == "hi there"
    assert m["ts"] is None          # the reader stamps now when there is no tag


def t_parse_twitch_privmsg_empty_display_name_falls_back():
    line = "@display-name=;id=x :bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :yo"
    assert bc.parse_twitch_privmsg(line)["user"] == "bob"


def t_parse_twitch_privmsg_ignores_non_privmsg():
    assert bc.parse_twitch_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!") is None
    assert bc.parse_twitch_privmsg("PING :tmi.twitch.tv") is None
    assert bc.parse_twitch_privmsg(":x!x@x PART #chan") is None


def t_parse_twitch_privmsg_garbage():
    assert bc.parse_twitch_privmsg("") is None
    assert bc.parse_twitch_privmsg(None) is None


# image emotes: emote_url_ok (#351)

def t_emote_url_ok_youtube_ggpht():
    assert bc.emote_url_ok("https://yt3.ggpht.com/abc/def-s48-w48") is True


def t_emote_url_ok_twitch_jtvnw():
    assert bc.emote_url_ok(
        "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/1.0") is True


def t_emote_url_ok_rejects_http():
    assert bc.emote_url_ok("http://yt3.ggpht.com/abc") is False


def t_emote_url_ok_rejects_foreign_host():
    # A look-alike that merely contains an allowed host as a substring must fail.
    assert bc.emote_url_ok("https://evil.example/yt3.ggpht.com/x") is False
    assert bc.emote_url_ok("https://ggpht.com.evil.example/x") is False


def t_emote_url_ok_rejects_non_string():
    assert bc.emote_url_ok(None) is False
    assert bc.emote_url_ok(123) is False


# image emotes: runs_to_tokens (YouTube, #351)

def t_runs_to_tokens_none_for_plain_text():
    assert bc.runs_to_tokens({"runs": [{"text": "hello world"}]}) is None


def t_runs_to_tokens_none_for_simpletext():
    assert bc.runs_to_tokens({"simpleText": "plain"}) is None


def t_runs_to_tokens_standard_emoji_stays_text():
    # A standard emoji is a glyph, not an image, so there are no tokens. (#345)
    msg = {"runs": [{"text": "gg "},
                    {"emoji": {"emojiId": "\U0001f605", "isCustomEmoji": False,
                               "shortcuts": [":sweat:"]}}]}
    assert bc.runs_to_tokens(msg) is None


def t_runs_to_tokens_custom_emote_image():
    msg = {"runs": [
        {"text": "nice "},
        {"emoji": {"emojiId": "UCabc/deadbeef", "isCustomEmoji": True,
                   "shortcuts": [":_pog:"],
                   "image": {"thumbnails": [
                       {"url": "https://yt3.ggpht.com/s/24"},
                       {"url": "https://yt3.ggpht.com/s/48"}]}}},
        {"text": "!"}]}
    toks = bc.runs_to_tokens(msg)
    assert toks == [
        {"t": "text", "v": "nice "},
        {"t": "emote", "url": "https://yt3.ggpht.com/s/48", "alt": ":_pog:"},
        {"t": "text", "v": "!"}]


def t_runs_to_tokens_custom_emote_no_image_falls_back():
    # With no image thumbnail there is nothing to render as <img>.
    msg = {"runs": [{"emoji": {"emojiId": "UCabc/x", "isCustomEmoji": True,
                               "shortcuts": [":_pog:"]}}]}
    assert bc.runs_to_tokens(msg) is None


def t_parse_chat_action_attaches_tokens_for_custom_emote():
    action = {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": "e1", "authorName": {"simpleText": "Al"},
        "message": {"runs": [
            {"text": "go "},
            {"emoji": {"emojiId": "UCx/y", "isCustomEmoji": True,
                       "shortcuts": [":_go:"],
                       "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/g"}]}}}]},
        "timestampUsec": "1700000000000000",
    }}}}
    msg = bc.parse_chat_action(action)
    assert msg["text"] == "go :_go:"            # flat fallback unchanged
    assert msg["tokens"][1] == {
        "t": "emote", "url": "https://yt3.ggpht.com/g", "alt": ":_go:"}


# image emotes: splice_twitch_emotes (#351)

def t_splice_twitch_emotes_none_without_tag():
    assert bc.splice_twitch_emotes("Kappa", "") is None
    assert bc.splice_twitch_emotes("Kappa", None) is None


def t_splice_twitch_emotes_single():
    toks = bc.splice_twitch_emotes("Kappa", "25:0-4")
    assert toks == [{"t": "emote",
                     "url": "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/1.0",
                     "alt": "Kappa"}]


def t_splice_twitch_emotes_text_around():
    toks = bc.splice_twitch_emotes("lol Kappa yes", "25:4-8")
    assert toks == [
        {"t": "text", "v": "lol "},
        {"t": "emote",
         "url": "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/1.0",
         "alt": "Kappa"},
        {"t": "text", "v": " yes"}]


def t_splice_twitch_emotes_multiple_ranges_same_id():
    toks = bc.splice_twitch_emotes("Kappa Kappa", "25:0-4,6-10")
    assert [t["t"] for t in toks] == ["emote", "text", "emote"]
    assert toks[1] == {"t": "text", "v": " "}


def t_splice_twitch_emotes_rejects_bad_id():
    # An id outside [A-Za-z0-9_] would corrupt the CDN URL, so the span stays text.
    assert bc.splice_twitch_emotes("Kappa", "ev.il:0-4") is None
    assert bc.splice_twitch_emotes("Kappa", "a b:0-4") is None


def t_splice_twitch_emotes_skips_out_of_range():
    assert bc.splice_twitch_emotes("hi", "25:0-99") is None


def t_parse_twitch_privmsg_attaches_emote_tokens():
    line = ("@display-name=Bob;emotes=25:4-8 "
            ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :lol Kappa")
    m = bc.parse_twitch_privmsg(line)
    assert m["text"] == "lol Kappa"
    assert m["tokens"] == [
        {"t": "text", "v": "lol "},
        {"t": "emote",
         "url": "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/1.0",
         "alt": "Kappa"}]


def t_parse_twitch_privmsg_no_emotes_no_tokens():
    line = "@display-name=Bob;emotes= :bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :hi"
    assert "tokens" not in bc.parse_twitch_privmsg(line)


# image emotes: sanitize_message carries/validates tokens (#351)

def _emote_tok(url, alt):
    return {"t": "emote", "url": url, "alt": alt}


def t_sanitize_message_carries_valid_tokens():
    raw = {"id": "1", "user": "Bob", "text": "hi :p:", "ts": 10.0,
           "tokens": [{"t": "text", "v": "hi "},
                      _emote_tok("https://yt3.ggpht.com/x", ":p:")]}
    out = bc.sanitize_message(raw, source="v")
    assert out["tokens"] == [{"t": "text", "v": "hi "},
                             {"t": "emote", "url": "https://yt3.ggpht.com/x", "alt": ":p:"}]


def t_sanitize_message_drops_tokens_without_emote():
    raw = {"id": "1", "user": "Bob", "text": "hi", "ts": 10.0,
           "tokens": [{"t": "text", "v": "hi"}]}
    assert "tokens" not in bc.sanitize_message(raw, source="v")


def t_sanitize_message_degrades_blocked_host_to_text():
    # An emote whose URL fails the host allowlist degrades to its alt text; with
    # no surviving emote token the flat text suffices, so no tokens are attached.
    raw = {"id": "1", "user": "Bob", "text": "hi :p:", "ts": 10.0,
           "tokens": [{"t": "text", "v": "hi "},
                      _emote_tok("https://evil.example/x", ":p:")]}
    assert "tokens" not in bc.sanitize_message(raw, source="v")


# relay BroadcastChatStore + endpoint

m = _load("irofeeds_bc", ("src", "relay", "racecast-feeds.py"))


def _raw(mid, user, text, ts):
    return {"id": mid, "user": user, "text": text, "ts": ts}


def t_store_add_sanitizes_and_tags_source():
    s = m.BroadcastChatStore()
    s.add_many("vidA", [_raw("1", "Bob", "hi\x07", 10.0)])
    msgs = s.data()["messages"]
    assert msgs == [{"ts": 10.0, "user": "Bob", "text": "hi", "source": "vidA"}]


def t_store_dedup_by_id():
    s = m.BroadcastChatStore()
    s.add_many("vidA", [_raw("1", "Bob", "hi", 10.0)])
    s.add_many("vidA", [_raw("1", "Bob", "hi", 10.0)])   # re-delivered continuation
    assert len(s.data()["messages"]) == 1


def t_store_merges_two_streams_in_ts_order():
    # On a producer handover vidA and vidB overlap into one ts-ordered stream.
    s = m.BroadcastChatStore()
    s.add_many("vidA", [_raw("a1", "Al", "from A", 30.0)])
    s.add_many("vidB", [_raw("b1", "Bo", "from B", 20.0)])
    msgs = s.data()["messages"]
    assert [mm["text"] for mm in msgs] == ["from B", "from A"]
    assert {mm["source"] for mm in msgs} == {"vidA", "vidB"}


def t_store_caps_at_max():
    s = m.BroadcastChatStore(cap=5)
    for i in range(20):
        s.add_many("v", [_raw(str(i), "U", f"m{i}", float(i))])
    msgs = s.data()["messages"]
    assert len(msgs) == 5
    assert msgs[-1]["text"] == "m19"
    assert msgs[0]["text"] == "m15"


def t_store_drops_unusable_rows():
    s = m.BroadcastChatStore()
    s.add_many("v", [_raw("1", "U", "   ", 10.0),   # empty text -> dropped
                     _raw("2", "U", "ok", None)])    # no ts -> dropped
    assert s.data()["messages"] == []


def _bc_client(broadcast_chat_store, supervisor=None):
    """make_handler over a real ThreadingHTTPServer. Returns (srv, get)."""
    import json as _json
    import threading as _t
    import urllib.error
    from urllib.request import urlopen

    class _StubFeed:
        def __init__(self, idx):
            self.idx = idx

    class _StubRelay:
        def __init__(self):
            self.feeds = {"A": _StubFeed(0), "B": _StubFeed(1)}

    handler = m.make_handler(_StubRelay(), broadcast_chat_store=broadcast_chat_store,
                             broadcast_chat_supervisor=supervisor)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path):
        try:
            with urlopen(base + path, timeout=5) as r:
                return r.status, _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read())

    return srv, get


def t_endpoint_returns_messages():
    s = m.BroadcastChatStore()
    s.add_many("v", [_raw("1", "Bob", "hello", 10.0)])
    srv, get = _bc_client(s)
    try:
        code, body = get("/broadcast-chat/data")
        assert code == 200
        assert body["messages"][0]["text"] == "hello"
        assert body["messages"][0]["source"] == "v"
    finally:
        srv.shutdown()


def t_endpoint_404_when_disabled():
    srv, get = _bc_client(None)
    try:
        code, body = get("/broadcast-chat/data")
        assert code == 404
        assert "error" in body
    finally:
        srv.shutdown()


def t_store_carries_tokens_through_to_data():
    s = m.BroadcastChatStore()
    raw = {"id": "1", "user": "Bob", "text": "hi :p:", "ts": 10.0,
           "tokens": [{"t": "text", "v": "hi "},
                      {"t": "emote", "url": "https://yt3.ggpht.com/x", "alt": ":p:"}]}
    s.add_many("v", [raw])
    msg = s.data()["messages"][0]
    assert msg["tokens"][1]["url"] == "https://yt3.ggpht.com/x"


def t_endpoint_returns_tokens():
    s = m.BroadcastChatStore()
    s.add_many("v", [{"id": "1", "user": "Bob", "text": "hi :p:", "ts": 10.0,
                      "tokens": [{"t": "emote", "url": "https://yt3.ggpht.com/x",
                                  "alt": ":p:"}]}])
    srv, get = _bc_client(s)
    try:
        code, body = get("/broadcast-chat/data")
        assert code == 200
        assert body["messages"][0]["tokens"][0]["alt"] == ":p:"
    finally:
        srv.shutdown()


def t_store_target_default_none():
    s = m.BroadcastChatStore()
    assert s.data().get("target") is None


def t_store_set_target_reflected_in_data():
    s = m.BroadcastChatStore()
    s.set_target({"platform": "youtube", "url": "https://x/live_chat?v=vid1"})
    assert s.data()["target"]["platform"] == "youtube"


def t_store_reset_clears_target():
    s = m.BroadcastChatStore()
    s.set_target({"platform": "twitch", "url": "https://x/popout/y/chat"})
    s.reset()
    assert s.data()["target"] is None


def t_bc_endpoint_includes_target():
    s = m.BroadcastChatStore()
    s.set_target({"platform": "twitch", "url": "https://www.twitch.tv/popout/x/chat"})
    srv, get = _bc_client(s)
    try:
        code, body = get("/broadcast-chat/data")
        assert code == 200
        assert body["target"]["platform"] == "twitch"
    finally:
        srv.shutdown()


def t_supervisor_sets_primary_target():
    class _StubReader:
        ended = False
        def start(self): return self
        def alive(self): return True
        def stop(self): pass
    s = m.BroadcastChatStore()
    sup = m.BroadcastChatSupervisor(s, None, None)
    sup.channel_source = type("C", (), {"refresh": lambda self: True})()
    sup._desired = lambda: {"vidAAAAAAAA": (_StubReader),
                            "twitch:foo": (_StubReader)}
    sup._cycle()
    assert s.data()["target"]["platform"] == "youtube"   # YouTube key is first


# rearm: the Refresh button recovers a frozen reader without waiting. (#294)

class _CountingReader:
    """A reader stub recording start()s; alive() follows the last start/stop."""
    def __init__(self, ended=False):
        self.ended = ended
        self._alive = not ended
        self.starts = 0
    def start(self): self.starts += 1; self._alive = True; return self
    def alive(self): return self._alive
    def stop(self): self._alive = False


def t_rearm_clears_tombstones():
    s = m.BroadcastChatStore()
    sup = m.BroadcastChatSupervisor(s, None, None)
    frozen = _CountingReader(ended=True)   # dead and tombstoned (#294)
    frozen._alive = False
    sup._readers = {"vidAAAAAAAAA": frozen}
    sup.rearm()
    assert frozen.ended is False           # tombstone dropped -> eligible to restart


def t_rearm_then_cycle_restarts_dead_reader():
    s = m.BroadcastChatStore()
    sup = m.BroadcastChatSupervisor(s, None, None)
    frozen = _CountingReader(ended=True)
    frozen._alive = False
    sup._readers = {"vidAAAAAAAAA": frozen}
    sup.rearm()
    # The stream is still live and desired, so the reconcile must restart it.
    sup.channel_source = type("C", (), {"refresh": lambda self: True})()
    fresh = _CountingReader(ended=False)
    sup._desired = lambda: {"vidAAAAAAAAA": (lambda: fresh)}
    sup._cycle()
    assert fresh.starts == 1                # a live, healthy reader is back


def t_reload_endpoint_triggers_rearm_and_returns_data():
    s = m.BroadcastChatStore()
    s.add_many("v", [_raw("1", "Bob", "hello", 10.0)])
    calls = []

    class _Sup:
        def rearm(self): calls.append(1)

    srv, get = _bc_client(s, supervisor=_Sup())
    try:
        code, body = get("/broadcast-chat/reload")
        assert code == 200
        assert body["messages"][0]["text"] == "hello"   # current mirror echoed back
        assert calls == [1]                               # server re-armed
    finally:
        srv.shutdown()


def t_reload_endpoint_404_when_disabled():
    srv, get = _bc_client(None)
    try:
        code, _ = get("/broadcast-chat/reload")
        assert code == 404
    finally:
        srv.shutdown()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - test harness reports all
                failures += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failures:
        print(f"\n{failures} failure(s)")
        raise SystemExit(1)
    print("broadcast_chat: all tests passed")
