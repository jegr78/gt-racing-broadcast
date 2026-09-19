#!/usr/bin/env python3
"""The one rule for "does this YouTube cookie jar hold a login?" (#615).

`racecast preflight` and the relay both ask it: preflight to warn before the
event, the relay at startup and when a resolve fails with yt-dlp's generic
'Requested format is not available'. A jar without these markers makes yt-dlp
treat the session as logged out and pick clients that offer no muxed rendition,
so the relay's selector finds nothing.

Pure stdlib, no sibling imports: the relay loads it too."""

COOKIE_MARKERS = ("SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO")

LOGGED_OUT_HINT = ("the YouTube cookie jar has no login; re-export it from a "
                   "logged-in browser with `racecast cookies <browser>`")


def text_has_login(text):
    """True when the Netscape jar *text* carries a logged-in YouTube session marker."""
    return any(marker in (text or "") for marker in COOKIE_MARKERS)


def jar_has_login(path):
    """True/False for the jar at *path*, None when there is no jar to judge (no path,
    no file, unreadable). None is "unknown", never "logged out"."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return text_has_login(fh.read())
    except OSError:
        return None
