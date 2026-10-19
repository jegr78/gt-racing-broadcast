"""YouTube output metrics; pasted URLs are parsed locally, never fetched."""

import re
from urllib.parse import urlparse, parse_qs, unquote

VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
HANDLE = re.compile(r"@[\w.\-·]{1,100}\Z")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def text(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Paste a YouTube URL or ID")
    return value.strip()


def parsed(value):
    if value.startswith(tuple(host + "/" for host in HOSTS)):
        value = "https://" + value
    try:
        url = urlparse(value)
        if (
            url.scheme not in ("http", "https")
            or url.hostname not in HOSTS
            or url.username is not None
            or url.password is not None
            or url.port is not None
        ):
            raise ValueError("Unsupported YouTube target URL")
    except ValueError:
        raise ValueError("Unsupported YouTube target URL") from None
    return url


def video_target(value):
    value = text(value)
    if not value:
        return None
    if VIDEO.fullmatch(value):
        return value
    url = parsed(value)
    path = url.path.strip("/")
    if url.hostname in ("youtu.be", "www.youtu.be"):
        candidate = path
    elif path == "watch":
        values = parse_qs(url.query).get("v", [])
        candidate = values[0] if len(values) == 1 else ""
    else:
        parts = path.split("/")
        candidate = (
            parts[1]
            if len(parts) == 2 and parts[0] in ("live", "shorts", "embed")
            else ""
        )
    if not VIDEO.fullmatch(candidate):
        raise ValueError("Select a concrete video, not a channel/live shortcut")
    return candidate


def channel_target(value):
    value = text(value)
    if not value:
        return None
    if CHANNEL.fullmatch(value):
        return {"id": value}
    if HANDLE.fullmatch(value):
        return {"forHandle": value}
    url = parsed(value)
    if url.hostname in ("youtu.be", "www.youtu.be"):
        raise ValueError("Paste a channel ID or @handle")
    path = unquote(url.path.strip("/"))
    if path.startswith("channel/") and CHANNEL.fullmatch(path[8:]):
        return {"id": path[8:]}
    if HANDLE.fullmatch(path):
        return {"forHandle": path}
    if path.startswith("user/") and re.fullmatch(r"[\w.\-]{1,100}", path[5:]):
        return {"forUsername": path[5:]}
    raise ValueError("Paste a channel ID or @handle")


def count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 2**64 - 1 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        number = int(value)
        return number if number <= 2**64 - 1 else None
    return None


def item(payload, expected):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid YouTube response")
    items = payload["items"]
    if not items:
        return None
    if (
        len(items) != 1
        or not isinstance(items[0], dict)
        or items[0].get("id") != expected
    ):
        raise ValueError("YouTube response target mismatch")
    return items[0]


def video_snapshot(payload, expected):
    data = item(payload, expected)
    if data is None:
        return "inaccessible", {"likes": None, "viewers": None}
    snippet = data.get("snippet", {})
    stats = data.get("statistics", {})
    live = data.get("liveStreamingDetails", {})
    if not all(isinstance(value, dict) for value in (snippet, stats, live)):
        raise ValueError("Invalid YouTube video")
    flag = snippet.get("liveBroadcastContent")
    if live.get("actualEndTime"):
        state = "ended"
    elif flag == "live":
        state = "live"
    elif not live.get("actualStartTime") and (
        flag == "upcoming" or live.get("scheduledStartTime")
    ):
        state = "upcoming"
    else:
        state = "not-live"
    return state, {
        "likes": count(stats.get("likeCount")),
        "viewers": count(live.get("concurrentViewers")) if state == "live" else None,
    }


def channel_snapshot(payload, expected):
    data = item(payload, expected)
    stats = data.get("statistics", {}) if data else {}
    if not isinstance(stats, dict):
        raise ValueError("Invalid YouTube channel")
    hidden = stats.get("hiddenSubscriberCount")
    value = None if hidden is not False else count(stats.get("subscriberCount"))
    return {
        "value": value,
        "exact": False,
        "supportsNet": False,
        "significantDigits": 3,
    }
