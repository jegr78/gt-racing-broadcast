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


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
