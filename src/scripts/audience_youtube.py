"""YouTube output metrics; pasted URLs are parsed locally, never fetched."""

import re
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http_util
from audience_admin import UserError
from urllib.parse import urlparse, parse_qs, unquote, urlencode

VIDEO = re.compile(r"[A-Za-z0-9_-]{11}\Z")
VIDEO_IDS = re.compile(r"[A-Za-z0-9_-]{11}(?:,[A-Za-z0-9_-]{11}){0,4}\Z")
CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
HANDLE = re.compile(r"@[\w.\-·]{1,100}\Z")
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def text(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(c) < 32 for c in value)
    ):
        raise UserError("Paste a YouTube URL or ID")
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
            raise UserError("Unsupported YouTube target URL")
    except ValueError:
        raise UserError("Unsupported YouTube target URL") from None
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
        raise UserError("Select a concrete video, not a channel/live shortcut")
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
        raise UserError("Paste a channel ID or @handle")
    path = unquote(url.path.strip("/"))
    if path.startswith("channel/") and CHANNEL.fullmatch(path[8:]):
        return {"id": path[8:]}
    if HANDLE.fullmatch(path):
        return {"forHandle": path}
    if path.startswith("user/") and re.fullmatch(r"[\w.\-]{1,100}", path[5:]):
        return {"forUsername": path[5:]}
    raise UserError("Paste a channel ID or @handle")


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


def _start_time(value):
    if not isinstance(value, str):
        raise ValueError("Invalid YouTube video")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid YouTube video") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def newest_live(payload, ids):
    """(video_id, title) of the live video in ids that started last, else None."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid YouTube response")
    best = None
    for data in payload["items"]:
        if not isinstance(data, dict):
            raise ValueError("Invalid YouTube video")
        snippet = data.get("snippet", {})
        live = data.get("liveStreamingDetails", {})
        if not isinstance(snippet, dict) or not isinstance(live, dict):
            raise ValueError("Invalid YouTube video")
        if (
            data.get("id") not in ids
            or snippet.get("liveBroadcastContent") != "live"
            or live.get("actualEndTime")
            or not live.get("actualStartTime")
        ):
            continue
        started = _start_time(live["actualStartTime"])
        title = snippet.get("title")
        if best is None or started > best[0]:
            best = (started, data["id"], title if isinstance(title, str) else "")
    return None if best is None else (best[1], best[2])


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


class APIError(Exception):
    def __init__(self, code, message, transient=False, retry_after=0):
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.retry_after = retry_after


def api_key(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{10,256}", value.strip()
    ):
        raise UserError("Enter a valid YouTube Data API key")
    return value.strip()


def retry_after(headers, default):
    value = headers.get("Retry-After", "") if headers else ""
    if not isinstance(value, str) or not value or len(value) > 128:
        return default
    try:
        seconds = int(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return default
            seconds = parsed.timestamp() - time.time()
        except (ValueError, TypeError, OSError, OverflowError):
            return default
    return max(1, min(900, seconds))


def _failure(error):
    reasons = set()
    try:
        data = json.loads(error.read(8192).decode("utf-8"))
        detail = data.get("error", {})
        rows = detail.get("errors", []) + detail.get("details", [])
        reasons = {
            r["reason"]
            for r in rows
            if isinstance(r, dict) and isinstance(r.get("reason"), str)
        }
    except (ValueError, TypeError, AttributeError, OSError):
        pass  # Provider bodies and exception messages are never diagnostics.
    if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
        return APIError(
            "quota", "YouTube API quota exhausted; retrying later", True, 900
        )
    if error.code == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
        return APIError(
            "rate_limit",
            "YouTube API rate limited; retrying later",
            True,
            retry_after(error.headers, 60),
        )
    if error.code >= 500 or 300 <= error.code < 400:
        return APIError(
            "temporary",
            "YouTube API temporarily unavailable",
            True,
            retry_after(error.headers, 60),
        )
    if error.code == 401 or reasons & {
        "keyInvalid",
        "apiKeyInvalid",
        "accessNotConfigured",
        "ipRefererBlocked",
        "API_KEY_INVALID",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
    }:
        return APIError(
            "auth", "Check the API key, restrictions and enabled YouTube Data API"
        )
    if error.code in (404, 410):
        return APIError("inaccessible", "Selected YouTube target is inaccessible")
    return APIError("denied", "YouTube denied access to the selected target")


def request(resource, params, key, open_url=None):
    """Call one fixed official resource; never follow redirects with the key."""
    allowed = (
        {"id": VIDEO_IDS}
        if resource == "videos"
        else {
            "id": CHANNEL,
            "forHandle": HANDLE,
            "forUsername": re.compile(r"[\w.\-]{1,100}\Z"),
        }
    )
    if (
        resource not in ("videos", "channels")
        or not isinstance(params, dict)
        or len(params) != 1
        or set(params) - set(allowed)
    ):
        raise ValueError("Select one supported YouTube target")
    name, value = next(iter(params.items()))
    if not isinstance(value, str) or not allowed[name].fullmatch(value):
        raise ValueError("Invalid YouTube target")
    key = api_key(key)
    parts = (
        "snippet,statistics,liveStreamingDetails"
        if resource == "videos"
        else "snippet,statistics"
    )
    url = (
        "https://www.googleapis.com/youtube/v3/"
        + resource
        + "?"
        + urlencode({"part": parts, **params})
    )
    opener = open_url or http_util.open_url
    try:
        with opener(
            url,
            headers={"x-goog-api-key": key, "Accept": "application/json"},
            timeout=5,
            allow_redirects=False,
        ) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("Oversized provider response")
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid provider response")
        return data
    except http_util.HTTPError as error:
        try:
            failure = _failure(error)
        finally:
            error.close()
        raise failure from None
    except (OSError, TimeoutError):
        raise APIError(
            "temporary", "YouTube API connection unavailable", True, 60
        ) from None
    except (ValueError, UnicodeError):
        raise APIError("temporary", "Invalid YouTube API response", True, 60) from None
