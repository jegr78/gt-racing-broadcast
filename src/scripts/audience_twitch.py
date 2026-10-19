"""Twitch output targets, OAuth metadata and Helix counts."""

import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, parse_qs, urlencode

ID = re.compile(r"[0-9]{1,25}\Z")
LOGIN = re.compile(r"[A-Za-z0-9_]{1,25}\Z")
CLIENT = re.compile(r"[A-Za-z0-9]{1,128}\Z")
HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}
SCOPE = "channel:read:subscriptions"


class APIError(Exception):
    def __init__(self, code, message, transient=False, retry_after=0):
        super().__init__(message)
        self.code, self.transient, self.retry_after = code, transient, retry_after


def _text(value, limit=2048):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid Twitch target or metadata")
    return value.strip()


def _rows(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid Twitch resource response")
    return payload["data"]


def channel_target(value):
    value = _text(value)
    if ID.fullmatch(value):
        return {"id": value}
    if value.startswith("@") and LOGIN.fullmatch(value[1:]):
        return {"login": value[1:].lower()}
    if LOGIN.fullmatch(value):
        return {"login": value.lower()}
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
            raise ValueError("Unsupported Twitch channel URL")
    except ValueError:
        raise ValueError("Unsupported Twitch channel URL") from None
    login = url.path.strip("/")
    if not LOGIN.fullmatch(login):
        raise ValueError("Select a concrete Twitch channel URL, login or ID")
    return {"login": login.lower()}


def channel_snapshot(payload, target):
    rows = _rows(payload)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("Selected Twitch channel was not found")
    row = rows[0]
    ident, login = row.get("id"), row.get("login")
    if (
        not isinstance(ident, str)
        or not ID.fullmatch(ident)
        or not isinstance(login, str)
        or not LOGIN.fullmatch(login)
        or not isinstance(target, dict)
        or (
            target.get("id") != ident
            if "id" in target
            else target.get("login") != login.lower()
        )
    ):
        raise ValueError("Twitch channel target mismatch")
    return {
        "id": ident,
        "login": login.lower(),
        "title": _text(row.get("display_name"), 512),
    }


def count(value):
    return value if type(value) is int and 0 <= value <= 2**64 - 1 else None


def stream_snapshot(payload, expected):
    rows = _rows(payload)
    if not rows:
        return "offline", None
    if (
        len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get("user_id") != expected
    ):
        raise ValueError("Twitch stream target mismatch")
    row = rows[0]
    return (
        ("live", count(row.get("viewer_count")))
        if row.get("type") == "live"
        else ("unavailable", None)
    )


def subscriptions_snapshot(payload, expected):
    rows = _rows(payload)
    if any(
        not isinstance(row, dict) or row.get("broadcaster_id") != expected
        for row in rows
    ):
        raise ValueError("Twitch subscription target mismatch")
    return count(payload.get("total"))


def _seconds(value, maximum=86400):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Invalid Twitch authorization timing")
    return value


def _scopes(value):
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(
            not isinstance(scope, str) or not re.fullmatch(r"[a-z0-9_:]{1,128}", scope)
            for scope in value
        )
    ):
        raise ValueError("Invalid Twitch authorization scopes")
    return sorted(set(value))


def validation_snapshot(payload, client):
    if not isinstance(payload, dict):
        raise ValueError("Invalid Twitch authorization")
    ident, login = payload.get("user_id"), payload.get("login")
    if (
        payload.get("client_id") != client
        or not isinstance(ident, str)
        or not ID.fullmatch(ident)
        or not isinstance(login, str)
        or not LOGIN.fullmatch(login)
    ):
        raise ValueError("Twitch authorization identity mismatch")
    return {
        "id": ident,
        "login": login.lower(),
        "scopes": _scopes(payload.get("scopes")),
        "expires": _seconds(payload.get("expires_in")),
    }


def _opaque(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ValueError("Invalid Twitch authorization data")
    return value


def device_snapshot(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid Twitch device authorization")
    device, user = (
        _opaque(payload.get("device_code")),
        _text(payload.get("user_code"), 32),
    )
    if not re.fullmatch(r"[A-Za-z0-9]{4,32}", user) or user == device:
        raise ValueError("Invalid Twitch public approval code")
    uri = _text(payload.get("verification_uri"))
    try:
        url = urlparse(uri)
        query = parse_qs(url.query, keep_blank_values=True)
        if (
            url.scheme != "https"
            or url.hostname != "www.twitch.tv"
            or url.path != "/activate"
            or url.username is not None
            or url.password is not None
            or url.port is not None
            or url.fragment
            or (url.query and query != {"public": ["true"], "device-code": [user]})
        ):
            raise ValueError("Unsupported Twitch approval URL")
    except ValueError:
        raise ValueError("Unsupported Twitch approval URL") from None
    return {
        "device": device,
        "user": user,
        "uri": uri,
        "expires": _seconds(payload.get("expires_in")),
        "interval": _seconds(payload.get("interval", 5), 300),
    }


def token_snapshot(payload):
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("token_type"), str)
        or payload["token_type"].lower() != "bearer"
    ):
        raise ValueError("Invalid Twitch token response")
    if "scope" in payload:
        scopes = payload["scope"]
        _scopes(scopes.split() if isinstance(scopes, str) else scopes)
    return {
        "access": _opaque(payload.get("access_token")),
        "refresh": _opaque(payload.get("refresh_token")),
        "expires": _seconds(payload.get("expires_in")),
    }


def client_id(value):
    value = _text(value, 128)
    if not CLIENT.fullmatch(value):
        raise ValueError("Enter a public Twitch application client ID")
    return value


def request_plan(operation, client, **values):
    client = client_id(client)
    schemas = {
        "device": set(),
        "exchange": {"device"},
        "refresh": {"refresh"},
        "validate": {"access"},
        "user": {"access", "target"},
        "viewers": {"access", "channel"},
        "subscriptions": {"access", "channel"},
    }
    if operation not in schemas or set(values) != schemas[operation]:
        raise ValueError("Unsupported Twitch request")
    headers, data, method = {"Accept": "application/json"}, None, "GET"
    if operation in ("device", "exchange", "refresh"):
        endpoint = "device" if operation == "device" else "token"
        url = "https://id.twitch.tv/oauth2/" + endpoint
        form = {"client_id": client}
        if operation == "refresh":
            form.update(
                grant_type="refresh_token", refresh_token=_opaque(values["refresh"])
            )
        else:
            form["scopes"] = SCOPE
            if operation == "exchange":
                form.update(
                    grant_type="urn:ietf:params:oauth:grant-type:device_code",
                    device_code=_opaque(values["device"]),
                )
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data, method = urlencode(form).encode("utf-8"), "POST"
    elif operation == "validate":
        url = "https://id.twitch.tv/oauth2/validate"
        headers["Authorization"] = "OAuth " + _opaque(values["access"])
    else:
        headers.update({
            "Authorization": "Bearer " + _opaque(values["access"]),
            "Client-Id": client,
        })
        if operation == "user":
            target = values["target"]
            if (
                not isinstance(target, dict)
                or len(target) != 1
                or set(target) - {"id", "login"}
            ):
                raise ValueError("Select one explicit Twitch channel")
            name, value = next(iter(target.items()))
            if not isinstance(value, str) or not (
                ID if name == "id" else LOGIN
            ).fullmatch(value):
                raise ValueError("Invalid Twitch channel")
            endpoint, query = "users", {name: value}
        else:
            channel = values["channel"]
            if not isinstance(channel, str) or not ID.fullmatch(channel):
                raise ValueError("Invalid Twitch channel")
            endpoint = "streams" if operation == "viewers" else "subscriptions"
            query = (
                {"user_id": channel}
                if operation == "viewers"
                else {"broadcaster_id": channel, "first": 1}
            )
        url = "https://api.twitch.tv/helix/" + endpoint + "?" + urlencode(query)
    return {
        "url": url,
        "headers": headers,
        "data": data,
        "method": method,
        "timeout": 5,
        "allow_redirects": False,
    }


def failure(operation, status, payload, headers, now=None):
    hints = (
        {
            payload.get(key)
            for key in ("error", "message")
            if isinstance(payload, dict)
            and isinstance(payload.get(key), str)
            and len(payload[key]) <= 128
        }
        if isinstance(payload, dict)
        else set()
    )
    if operation == "exchange" and status == 400:
        if "authorization_pending" in hints:
            return APIError("pending", "Waiting for Twitch authorization", True)
        if "slow_down" in hints:
            return APIError("slow_down", "Wait before checking Twitch again", True)
    if status == 429:
        now = time.time() if now is None else now
        delay = 60
        normalized = {str(k).lower(): v for k, v in headers.items()}
        value = normalized.get("retry-after")
        try:
            if value is not None:
                try:
                    delay = int(value)
                except (ValueError, TypeError):
                    delay = int(parsedate_to_datetime(value).timestamp() - now)
            elif "ratelimit-reset" in normalized:
                delay = int(float(normalized["ratelimit-reset"]) - now)
        except (ValueError, TypeError, OverflowError, AttributeError):
            delay = 60
        return APIError(
            "rate_limit", "Twitch rate limit reached", True, max(1, min(900, delay))
        )
    if status == 401 or operation == "refresh" and status in (400, 403):
        return APIError("auth", "Reconnect the Twitch account")
    if status == 403:
        return APIError(
            "permission", "Twitch permission is unavailable for this metric"
        )
    if operation == "device" and status == 400:
        return APIError("setup", "Check the public Twitch application client ID")
    if status == 404:
        return APIError("inaccessible", "Twitch resource is unavailable")
    if status >= 500 or 300 <= status < 400:
        return APIError("temporary", "Twitch temporarily unavailable", True, 60)
    return APIError("denied", "Twitch did not authorize this request")
