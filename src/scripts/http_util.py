#!/usr/bin/env python3
"""The one place racecast's CLI/scripts side issues outbound HTTP.

Every request carries an explicit User-Agent: Cloudflare-fronted hosts (Discord,
Google Fonts, some vendor endpoints) reject the default `Python-urllib/x.y` UA with
HTTP 403, so a bare urllib call silently fails. Routing all covered-module HTTP
through here makes "forgetting the UA" structurally impossible (enforced by
tests/test_http_util.py). The relay and the self-contained get-*/setup-assets
scripts are excluded and keep their own UA, because they stay dependency-light."""
import json
import uuid
import urllib.error
from urllib.request import Request, urlopen   # module-level so tests can patch http_util.urlopen

RACECAST_UA = "racecast/1.0"
DEFAULT_TIMEOUT = 10
HTTPError = urllib.error.HTTPError            # re-export: callers never import urllib to catch


def open_url(url, *, data=None, headers=None, method=None, timeout=DEFAULT_TIMEOUT):
    """Return the urllib response (use in a `with`). RACECAST_UA is always set; a
    caller-supplied User-Agent in `headers` overrides it. Raises HTTPError on
    4xx/5xx exactly like urllib. `timeout=None` means no timeout."""
    merged = {"User-Agent": RACECAST_UA}
    if headers:
        merged.update(headers)
    req = Request(url, data=data, headers=merged, method=method)
    return urlopen(req, timeout=timeout)        # noqa: S310 (UA-stamped; covered-module HTTP funnels here)


def get_bytes(url, *, headers=None, timeout=DEFAULT_TIMEOUT):
    with open_url(url, headers=headers, timeout=timeout) as r:
        return r.read()


def get_json(url, *, headers=None, timeout=DEFAULT_TIMEOUT):
    return json.loads(get_bytes(url, headers=headers, timeout=timeout).decode("utf-8"))


def post_json(url, obj, *, headers=None, timeout=DEFAULT_TIMEOUT):
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    with open_url(url, data=json.dumps(obj).encode("utf-8"), headers=merged,
                  method="POST", timeout=timeout) as r:
        return r.read()


def post_multipart(url, fields=None, files=None, *, headers=None, timeout=DEFAULT_TIMEOUT):
    """POST a multipart/form-data body with RACECAST_UA always set. `fields` is
    {name: str}; `files` is
    [(field_name, filename, content_bytes_or_str, content_type)]."""
    boundary = "----racecast" + uuid.uuid4().hex
    body = bytearray()

    def _w(text):
        body.extend(text.encode("utf-8"))

    for name, value in (fields or {}).items():
        _w(f"--{boundary}\r\n")
        _w(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        _w(f"{value}\r\n")
    for field_name, filename, content, ctype in (files or []):
        _w(f"--{boundary}\r\n")
        _w(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n')
        _w(f"Content-Type: {ctype}\r\n\r\n")
        body.extend(content.encode("utf-8") if isinstance(content, str) else content)
        _w("\r\n")
    _w(f"--{boundary}--\r\n")

    merged = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if headers:
        merged.update(headers)
    with open_url(url, data=bytes(body), headers=merged, method="POST", timeout=timeout) as r:
        return r.read()
