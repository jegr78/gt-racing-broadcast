#!/usr/bin/env python3
"""Pure plumbing for the /console/buttons reverse proxy to Bitfocus Companion (#236).

No I/O, no sockets: header/path transforms plus the address/version decisions the
relay's _proxy_companion uses. Companion >= v4.1.0 serves its UI under a sub-path when
the proxy injects the `Companion-custom-prefix` header WITHOUT a leading slash
(bitfocus/companion #3503). These helpers know nothing about tRPC/WebSocket framing, so a
Companion upgrade does not affect them. Tests: tests/test_console_proxy.py."""
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

MOUNT_PREFIX = "/console/buttons"          # the relay path prefix (strip / route)
PREFIX_HEADER_VALUE = "console/buttons"    # the Companion-custom-prefix value (NO leading slash)
COMPANION_PREFIX_HEADER = "Companion-custom-prefix"
RELAY_COOKIE = "rc_console"               # the relay's auth cookie; must never reach Companion

# RFC 7230 hop-by-hop headers (lowercase), never forwarded on the HTTP path.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "trailers", "transfer-encoding", "upgrade"}


def scrub_relay_cookie(cookie_header):
    """Remove the relay's rc_console auth cookie from a Cookie header value, preserving any
    other cookies. Returns the cleaned header, or None if nothing remains (drop the header)."""
    if not cookie_header:
        return None
    kept = [c.strip() for c in cookie_header.split(";")
            if c.strip() and c.split("=", 1)[0].strip() != RELAY_COOKIE]
    return "; ".join(kept) if kept else None


def strip_relay_token(request_path):
    """Remove the relay's `t` auth-token query param before forwarding upstream: the relay
    credential must never reach Companion. Path and all other query params are preserved."""
    parts = urlsplit(request_path)
    if not parts.query:
        return request_path
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "t"]
    return urlunsplit(("", "", parts.path, urlencode(kept), ""))


def upstream_path(request_path):
    """Map a full '/console/buttons[/...]' request path (optional ?query) to the Companion
    upstream path. The bare prefix and '<prefix>/' both map to '/'."""
    parts = urlsplit(request_path); path, pre = parts.path, MOUNT_PREFIX
    if path in (pre, pre + "/"):
        up = "/"
    elif path.startswith(pre + "/"):
        up = path[len(pre):]
    else:
        up = path
    return urlunsplit(("", "", up, parts.query, ""))


def forward_request_headers(headers, prefix=PREFIX_HEADER_VALUE, host="127.0.0.1:8000"):
    """Client headers to send upstream on the HTTP path: drop hop-by-hop, the original Host,
    and Accept-Encoding (Companion then replies uncompressed; the proxy does not re-encode);
    set Host and inject the no-leading-slash sub-path prefix header. `headers` exposes
    .items() (a dict or http.server's email.message.Message)."""
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in ("host", "accept-encoding"):
            continue
        if lk == "cookie":
            cleaned = scrub_relay_cookie(v)
            if cleaned:
                out[k] = cleaned
            # else: drop the header entirely, nothing useful remains
            continue
        out[k] = v
    out["Host"] = host
    out[COMPANION_PREFIX_HEADER] = prefix
    return out


def filter_response_headers(items):
    """Upstream response headers to relay back: drop hop-by-hop and the framing headers the
    proxy recomputes (Content-Length/Type) or forced off (Content-Encoding). Also drop
    x-frame-options so the /console/buttons wrapper can embed Companion in a same-origin
    iframe (the wrapper and the proxied content share the relay origin; the director gate
    upstream is the real boundary, not a frame-busting header)."""
    out = []
    for k, v in items:
        lk = k.lower()
        if lk in HOP_BY_HOP or lk in ("content-length", "content-type", "content-encoding",
                                      "x-frame-options"):
            continue
        out.append((k, v))
    return out


def is_websocket_upgrade(headers):
    """True for a WebSocket upgrade request (Companion's tRPC /trpc channel)."""
    return (headers.get("Upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("Connection", "").lower())


def version_ge(ver_str, floor):
    """True if dotted ver_str (e.g. '4.1.0') >= floor tuple; False on None/unparseable."""
    try:
        parts = tuple(int(x) for x in ver_str.split(".")[:3])
    except (AttributeError, ValueError):
        return False
    return parts >= floor


def resolve_companion_base(bind_ip, tailscale_ip, port=8000):
    """Pick the local Companion admin base URL. A specific bind_ip (Companion bound to one
    interface, e.g. the Tailscale IP) is authoritative; 0.0.0.0/empty -> loopback; a missing
    bind_ip -> the Tailscale IP if known, else loopback."""
    host = (bind_ip or "").strip()
    if host and host != "0.0.0.0":
        pass
    elif host == "0.0.0.0":
        host = "127.0.0.1"
    else:
        host = (tailscale_ip or "").strip() or "127.0.0.1"
    return "http://%s:%d" % (host, port)
