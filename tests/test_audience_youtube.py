"""Offline YouTube output-target, metric and connection behavior."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
import audience_youtube as yt

VIDEO = "abcdefghijk"
CHANNEL = "UC" + "a" * 22


def video(**patch):
    return {
        "id": VIDEO,
        "snippet": {
            "title": "Output race",
            "channelId": CHANNEL,
            "channelTitle": "Producer",
            "liveBroadcastContent": "live",
        },
        "statistics": {"likeCount": "0"},
        "liveStreamingDetails": {"actualStartTime": "start", "concurrentViewers": "0"},
        **patch,
    }


def rejects(fn, value):
    try:
        fn(value)
    except ValueError:
        return
    raise AssertionError("Invalid output target was accepted")


def t_targets_are_concrete_explicit_and_never_arbitrary_urls():
    for value in (
        VIDEO,
        " https://youtu.be/" + VIDEO,
        "https://www.youtube.com/watch?v=" + VIDEO + "&t=20",
        "youtube.com/live/" + VIDEO,
        "https://m.youtube.com/shorts/" + VIDEO,
    ):
        assert yt.video_target(value) == VIDEO, value
    assert yt.video_target("") is None
    assert yt.channel_target(CHANNEL) == {"id": CHANNEL}
    assert yt.channel_target("https://youtube.com/channel/" + CHANNEL) == {
        "id": CHANNEL
    }
    assert yt.channel_target("https://www.youtube.com/@Racing.Überblick") == {
        "forHandle": "@Racing.Überblick"
    }
    assert yt.channel_target("https://youtube.com/user/ClassicRacing") == {
        "forUsername": "ClassicRacing"
    }
    for value in (
        "https://example.invalid/watch?v=" + VIDEO,
        "https://youtube.com.evil.invalid/watch?v=" + VIDEO,
        "https://person@youtube.com/watch?v=" + VIDEO,
        "https://youtube.com/@race/live",
        "https://youtube.com/channel/" + CHANNEL + "/live",
        "https://youtube.com/watch?v=" + VIDEO + "&v=another1234",
        "https://youtu.be/" + VIDEO + "/extra",
        "file:///tmp/output",
        True,
    ):
        rejects(yt.video_target, value)
    for value in (
        "https://example.invalid/@race",
        "https://youtube.com/c/custom",
        "https://youtube.com/@race/live",
        "@../not-a-handle",
    ):
        rejects(yt.channel_target, value)


def t_lifecycle_and_missing_counts_are_distinct_from_real_zero():
    assert yt.video_snapshot({"items": [video()]}, VIDEO) == (
        "live",
        {"likes": 0, "viewers": 0},
    )
    assert yt.video_snapshot(
        {
            "items": [
                video(
                    liveStreamingDetails={
                        "actualStartTime": "start",
                        "actualEndTime": "end",
                        "concurrentViewers": "123",
                    }
                )
            ]
        },
        VIDEO,
    ) == ("ended", {"likes": 0, "viewers": None})
    state, values = yt.video_snapshot(
        {
            "items": [
                video(
                    snippet={"liveBroadcastContent": "upcoming"},
                    liveStreamingDetails={"scheduledStartTime": "future"},
                )
            ]
        },
        VIDEO,
    )
    assert state == "upcoming" and values["viewers"] is None
    assert yt.video_snapshot({"items": []}, VIDEO) == (
        "inaccessible",
        {"likes": None, "viewers": None},
    )
    assert (
        yt.video_snapshot(
            {
                "items": [
                    video(
                        snippet={"liveBroadcastContent": "none"},
                        liveStreamingDetails={
                            "actualStartTime": "start",
                            "scheduledStartTime": "past",
                        },
                    )
                ]
            },
            VIDEO,
        )[0]
        == "not-live"
    ), "A started broadcast must not become upcoming again"
    for invalid in (None, True, -1, "-1", "0.5", float("nan"), 2**64):
        assert yt.video_snapshot(
            {
                "items": [
                    video(
                        statistics={"likeCount": invalid},
                        liveStreamingDetails={"concurrentViewers": invalid},
                    )
                ]
            },
            VIDEO,
        )[1] == {"likes": None, "viewers": None}, invalid
    rejects(
        lambda payload: yt.video_snapshot(payload, VIDEO), {"items": [{"id": "wrong"}]}
    )


def t_subscribers_remain_approximate_and_hidden_is_unavailable():
    for stats, expected in (
        ({"subscriberCount": "29500", "hiddenSubscriberCount": False}, 29500),
        ({"subscriberCount": "0", "hiddenSubscriberCount": False}, 0),
        ({"subscriberCount": "29500", "hiddenSubscriberCount": True}, None),
        ({"hiddenSubscriberCount": False}, None),
        ({"subscriberCount": "29500"}, None),
    ):
        assert yt.channel_snapshot(
            {"items": [{"id": CHANNEL, "statistics": stats}]}, CHANNEL
        ) == {
            "value": expected,
            "exact": False,
            "supportsNet": False,
            "significantDigits": 3,
        }


class Response:
    def __init__(self, body):
        self.body = body
        self.reads = []

    def read(self, limit=-1):
        self.reads.append(limit)
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def t_transport_uses_fixed_api_headers_timeout_and_bounded_reads():
    import json

    calls = []
    response = Response(b'{"items":[]}')

    def opener(url, **options):
        calls.append((url, options))
        return response

    key = "fixture-local-api-key"
    assert yt.request("videos", {"id": VIDEO}, key, open_url=opener) == {"items": []}
    url, options = calls[0]
    assert url.startswith("https://www.googleapis.com/youtube/v3/videos?")
    assert "id=" + VIDEO in url and key not in url
    assert options["headers"]["x-goog-api-key"] == key
    assert options["timeout"] == 5 and options["allow_redirects"] is False
    assert response.reads == [1024 * 1024 + 1]
    for resource, params in (
        ("https://example.invalid", {"id": VIDEO}),
        ("videos", {"id": "https://example.invalid"}),
        ("channels", {"id": CHANNEL, "forHandle": "@race"}),
    ):
        try:
            yt.request(resource, params, key, open_url=opener)
        except ValueError:
            pass  # Unsupported resource/target must fail before outbound work.
        else:
            raise AssertionError("Unsafe target reached transport")
    assert len(calls) == 1
    for body in (b"x" * (1024 * 1024 + 1), b"not JSON", json.dumps([1]).encode()):
        try:
            yt.request(
                "videos",
                {"id": VIDEO},
                key,
                open_url=lambda *_args, _body=body, **_opts: Response(_body),
            )
        except yt.APIError as error:
            assert error.transient and key not in str(error)
        else:
            raise AssertionError("Oversized/invalid provider response accepted")


def t_transport_error_classification_never_exposes_provider_bodies():
    import io
    import json
    import http_util

    secret = "fixture-local-secret-must-not-leak"
    for status, reason, expected, transient, retry in (
        (403, "quotaExceeded", "quota", True, 900),
        (429, "", "rate_limit", True, 17),
        (503, "", "temporary", True, 60),
        (400, "keyInvalid", "auth", False, 0),
        (403, "forbidden", "denied", False, 0),
        (404, "videoNotFound", "inaccessible", False, 0),
        (302, "", "temporary", True, 60),
    ):
        payload = {"error": {"message": secret, "errors": [{"reason": reason}]}}

        def opener(*_args, _status=status, _payload=payload, **_options):
            headers = {"Retry-After": "17"} if _status == 429 else {}
            raise http_util.HTTPError(
                "https://www.googleapis.com/youtube/v3/videos",
                _status,
                secret,
                headers,
                io.BytesIO(json.dumps(_payload).encode()),
            )

        try:
            yt.request("videos", {"id": VIDEO}, secret, open_url=opener)
        except yt.APIError as error:
            assert error.code == expected and error.transient is transient, repr(error)
            assert error.retry_after == retry, (status, error.retry_after)
            assert secret not in str(error) and error.__suppress_context__
        else:
            raise AssertionError("Failed API call treated as success")


def t_invalid_key_details_override_generic_request_error():
    import io
    import http_util

    def opener(*_args, **_options):
        raise http_util.HTTPError(
            "https://www.googleapis.com/youtube/v3/videos",
            400,
            "fixture",
            {},
            io.BytesIO(
                b'{"error":{"errors":[{"reason":"badRequest"}],'
                b'"details":[{"reason":"API_KEY_INVALID"}]}}'
            ),
        )

    try:
        yt.request("videos", {"id": VIDEO}, "fixture-local-api-key", open_url=opener)
    except yt.APIError as error:
        assert error.code == "auth", error.code
    else:
        raise AssertionError("Invalid key was accepted")


def t_request_accepts_up_to_five_video_ids():
    urls = []

    def opener(url, **_options):
        urls.append(url)
        return Response(b'{"items":[]}')

    key = "fixture-local-api-key"
    yt.request("videos", {"id": "aaaaaaaaaaa,bbbbbbbbbbb"}, key, open_url=opener)
    assert "id=aaaaaaaaaaa%2Cbbbbbbbbbbb" in urls[0]
    five = ",".join(c * 11 for c in "abcde")
    yt.request("videos", {"id": five}, key, open_url=opener)
    assert len(urls) == 2
    for bad in (",".join(c * 11 for c in "abcdef"), "aaaaaaaaaaa,", ""):
        try:
            yt.request("videos", {"id": bad}, key, open_url=opener)
        except ValueError:
            pass  # Rejected before any outbound work.
        else:
            raise AssertionError("Invalid video id list accepted: " + bad)
    assert len(urls) == 2


def _live_item(video_id, flag="live", start=None, end=None, title="T"):
    details = {}
    if start:
        details["actualStartTime"] = start
    if end:
        details["actualEndTime"] = end
    return {
        "id": video_id,
        "snippet": {"liveBroadcastContent": flag, "title": title + video_id[:1]},
        "liveStreamingDetails": details,
    }


def t_newest_live_picks_the_later_start():
    a, b = "A" * 11, "B" * 11
    payload = {
        "items": [
            _live_item(a, start="2026-10-23T18:00:00Z"),
            _live_item(b, start="2026-10-23T18:05:00Z"),
        ]
    }
    assert yt.newest_live(payload, [a, b]) == (b, "TB")
    assert yt.newest_live(payload, [a]) == (a, "TA")


def t_newest_live_ignores_upcoming_and_ended():
    a, b, c, d = "A" * 11, "B" * 11, "C" * 11, "D" * 11
    upcoming = _live_item(a, flag="upcoming")
    ended = _live_item(
        b, start="2026-10-23T17:00:00Z", end="2026-10-23T17:30:00Z"
    )
    live = _live_item(c, start="2026-10-23T16:00:00Z")
    stranger = _live_item(d, start="2026-10-23T19:00:00Z")
    got = yt.newest_live({"items": [upcoming, ended, live, stranger]}, [a, b, c])
    assert got == (c, "TC")
    assert yt.newest_live({"items": [upcoming, ended]}, [a, b]) is None
    assert yt.newest_live({"items": []}, [a]) is None
    for bad in (None, {}, {"items": [1]}, {"items": [{"id": a, "snippet": 1}]}):
        try:
            yt.newest_live(bad, [a])
        except ValueError:
            pass  # Malformed payloads are rejected.
        else:
            raise AssertionError("Malformed payload accepted")


def t_newest_live_skips_videos_of_another_channel():
    a, b = "A" * 11, "B" * 11
    mine, other = "UC" + "m" * 22, "UC" + "o" * 22
    own = _live_item(a, start="2026-10-23T18:00:00Z")
    own["snippet"]["channelId"] = mine
    foreign = _live_item(b, start="2026-10-23T19:00:00Z")
    foreign["snippet"]["channelId"] = other
    payload = {"items": [own, foreign]}
    assert yt.newest_live(payload, [a, b]) == (b, "TB")
    assert yt.newest_live(payload, [a, b], channel=mine) == (a, "TA")
    assert yt.newest_live({"items": [foreign]}, [b], channel=mine) is None


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
