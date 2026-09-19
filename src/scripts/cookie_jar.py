#!/usr/bin/env python3
"""The one rule for "does this YouTube cookie jar hold a login?" (#615).

`racecast preflight` and the relay both ask it: preflight to warn before the
event, the relay at startup and when a resolve fails with yt-dlp's generic
'Requested format is not available'. A jar without these markers makes yt-dlp
treat the session as logged out and pick clients that offer no muxed rendition,
so the relay's selector finds nothing.

It also holds the export filter (#616): yt-dlp's --cookies-from-browser writes
every cookie of the browser profile into the jar (GitHub, Discord, the Google
account set, ...). Both exports write that raw file into private_export_path()
and filter_jar() moves only the platform's own domains onto the real jar.

Pure stdlib, no sibling imports: the relay loads it too."""
import contextlib
import os
import shutil
import tempfile

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


# The domains a platform's jar keeps, each with its subdomains. YouTube needs only
# youtube.com: requests to youtube.com never carry .google.com cookies, and a live
# resolve with the youtube.com cookies alone was measured to work (#616).
PLATFORM_COOKIE_DOMAINS = {
    "youtube": ("youtube.com",),
    "twitch": ("twitch.tv",),
}

_HTTPONLY_PREFIX = "#HttpOnly_"
# Line breaks that str.splitlines() knows and yt-dlp's jar loader does not. A cookie
# value may carry them, so a line holding one is dropped rather than split (#616).
_FOREIGN_BREAKS = frozenset("\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _cookie_domain(line):
    """The domain of a Netscape cookie line, or None when *line* is not a cookie."""
    fields = line.rstrip("\n").split("\t")
    if len(fields) != 7:
        return None
    domain = fields[0]
    if domain.startswith(_HTTPONLY_PREFIX):
        domain = domain[len(_HTTPONLY_PREFIX):]
    return domain.lstrip(".").lower()


def _domain_allowed(domain, allowed):
    return any(domain == base or domain.endswith("." + base) for base in allowed)


def _has_control_char(line):
    return any((ch < " " and ch not in "\t\n") or ch in _FOREIGN_BREAKS for ch in line)


def filter_jar_text(text, platform):
    """(kept_text, dropped_count): the Netscape jar *text* with only *platform*'s
    cookies. Lines split on "\n" only, as yt-dlp's loader reads them: splitting on
    more would let a foreign cookie's value smuggle in a platform line. Comment and
    blank lines stay; a cookie of another domain, a cookie line with a control
    character, and any line that is neither a comment nor a cookie are dropped.
    Raises ValueError for an unknown platform rather than keeping everything."""
    if platform not in PLATFORM_COOKIE_DOMAINS:
        raise ValueError(f"no cookie domains known for platform {platform!r}")
    allowed = PLATFORM_COOKIE_DOMAINS[platform]
    kept, dropped = [], 0
    for line in (text or "").split("\n"):
        if not line:
            continue
        line += "\n"
        domain = _cookie_domain(line)
        if domain is None:
            is_comment = line.startswith("#") and not line.startswith(_HTTPONLY_PREFIX)
            if is_comment or not line.strip():
                kept.append(line)
            else:
                dropped += 1
        elif _domain_allowed(domain, allowed) and not _has_control_char(line):
            kept.append(line)
        else:
            dropped += 1
    return "".join(kept), dropped


@contextlib.contextmanager
def private_export_path(out):
    """A path for yt-dlp's raw browser export, inside a fresh owner-only directory
    next to the real jar *out*; the directory is removed on exit. The raw export
    holds every site's cookies, so it never lands on the jar the relay reads.
    Raises OSError when the directory cannot be created."""
    tmpdir = tempfile.mkdtemp(prefix=".cookie-export-",
                              dir=os.path.dirname(os.path.realpath(out)))
    try:
        yield os.path.join(tmpdir, os.path.basename(out))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def filter_jar(path, platform, dest=None):
    """Write the jar at *path* with only *platform*'s cookies to *dest* (default:
    *path* itself), owner-only. A symlinked *dest* is written through to its target.
    Returns the number of dropped lines, or None when nothing was written: no path,
    an unreadable jar, or a failed write. The write goes through a temp file next to
    *dest*, so a failure leaves *dest* as it was, never half-written."""
    if not path:
        return None
    dest = os.path.realpath(dest or path)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            text = fh.read()
    except OSError:
        return None
    kept, dropped = filter_jar_text(text, platform)
    if not dropped and dest == os.path.realpath(path):
        return 0
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".cookies-", dir=os.path.dirname(dest))
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape",
                       newline="") as fh:
            fh.write(kept)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass   # best-effort hardening; mkstemp already created it owner-only
        os.replace(tmp, dest)
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass   # the temp file is already gone
        return None
    return dropped
