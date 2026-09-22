"""Console auth core (#216): pure and stdlib-only, importable by both the relay
(src/relay/racecast-feeds.py) and the CLI (src/racecast.py) WITHOUT importing the
hyphenated relay module.

Token model:
    token = "<streamer_key>.<version>.<sig>"
    streamer_key = streamer_key(name)                 # URL-safe [a-z0-9-]
    sig = HMAC_SHA256(secret, "<streamer_key>:<version>") hex, truncated to 128 bits.
A valid signature IS proof the request is that streamer, so no token->name map is
stored. Revocation is a per-streamer integer version (see console_admin.py): a token
whose version is below the streamer's current version is rejected.
"""
import hashlib
import hmac
import re
import threading
import time
from http.cookies import SimpleCookie

COOKIE_NAME = "rc_console"
_KEY_RE = re.compile(r"[a-z0-9-]+")
# A minted token is "<streamer_key>.<version>.<sig>" with a [a-z0-9-] key, digit
# version and hex sig, so the whole token can only ever be [A-Za-z0-9._-].
_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")


def streamer_key(s):
    """Normalize a streamer name to a URL-safe key. Pinned to
    racecast-feeds.asset_key() by a cross-check in tests/test_cockpit.py, so keep
    the two in sync."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"[^a-z0-9-]", "", s)


def _sign(secret, key, version):
    msg = f"{key}:{version}".encode("utf-8")
    full = hmac.new((secret or "").encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return full[:32]                       # 128 bits keeps the link short


def mint_token(secret, key, version=1):
    """Build a signed token for an already-normalized streamer_key."""
    return f"{key}.{int(version)}.{_sign(secret, key, int(version))}"


def verify_token(secret, token, versions=None):
    """Return the streamer_key iff the token's signature is valid (constant-time)
    AND, when *versions* is given, its version is current. None on any failure.
    *versions* is the {streamer_key: current_version} dict (default 1 when absent)."""
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    key, ver_s, sig = parts
    if not key or not _KEY_RE.fullmatch(key):
        return None
    try:
        version = int(ver_s)
    except ValueError:
        return None
    expected = _sign(secret, key, version)
    if not hmac.compare_digest(sig, expected):
        return None
    if versions is not None and version < int(versions.get(key, 1)):
        return None
    return key


def secret_matches(presented, secret):
    """Constant-time compare of a presented secret against the configured league
    secret. Gates the producer-only /cockpit/versions takeover pull, which is
    reachable via Funnel and therefore must authenticate. (#191)"""
    return hmac.compare_digest(presented or "", secret or "")


def safe_cookie_token(token):
    """Return *token* iff it holds only the characters a minted token can contain
    ([A-Za-z0-9._-]), else "". A barrier against HTTP response splitting and cookie
    injection (CWE-113/CWE-20): the value written to the Set-Cookie header is never
    raw request input, even though it has already passed verify_token(). The
    allowlist excludes CR/LF, ';', whitespace and every other header-control
    character."""
    return token if token and _TOKEN_RE.fullmatch(token) else ""


def parse_cookie_token(cookie_header, cookie_name=COOKIE_NAME):
    """Extract a named cookie's value from a raw Cookie header, or None.
    Defaults to the rc_console auth cookie; callers such as the OAuth state
    cookie may pass another cookie_name."""
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(cookie_name)
    return morsel.value if morsel else None


class RateLimiter:
    """Fixed-window per-key counter (auth failures, chat sends). Time-injectable
    so tests are deterministic. Best-effort, in-process only."""

    def __init__(self, limit, window_s):
        self.limit = limit
        self.window_s = window_s
        self._hits = {}                    # key -> (window_start, count)
        self._lock = threading.Lock()      # ThreadingHTTPServer = one thread/request

    def allow(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:                   # read-modify-write must be atomic
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window_s:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            return count <= self.limit
