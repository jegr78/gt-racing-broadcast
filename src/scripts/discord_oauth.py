"""Discord OAuth2 (Authorization Code, scope=identify) helpers for /console login.

Pure and stdlib only. The relay (src/relay/racecast-feeds.py) calls these to build
the authorize redirect, sign and verify a stateless CSRF `state`, and resolve a
Discord username to a Crew member. The two network calls (code->token, /users/@me)
are a thin wrapper the relay owns; response PARSING stays here so tests run offline.

This is NOT a bot: scope is strictly `identify` (no email, guilds, or message
access). The app is registered per-league in the Discord Developer Portal; the
client_id/secret live in profiles/<name>/profile.env.
"""
import hashlib
import hmac
import re
from urllib.parse import urlencode

AUTHORIZE_ENDPOINT = "https://discord.com/oauth2/authorize"
TOKEN_ENDPOINT = "https://discord.com/api/oauth2/token"
USERINFO_ENDPOINT = "https://discord.com/api/users/@me"

# MagicDNS host: dot-separated DNS labels (no hyphen at a label boundary) ending in .ts.net.
_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+ts\.net\Z", re.IGNORECASE)
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


def authorize_url(client_id, redirect_uri, state):
    """Build the Discord authorize URL (scope=identify, response_type=code)."""
    q = urlencode({
        "client_id": client_id or "",
        "redirect_uri": redirect_uri or "",
        "response_type": "code",
        "scope": "identify",
        "state": state or "",
        "prompt": "none",
    })
    return f"{AUTHORIZE_ENDPOINT}?{q}"


def _sign_state(secret, ts, nonce):
    msg = f"{int(ts)}.{nonce}".encode("utf-8")
    return hmac.new((secret or "").encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def sign_state(secret, nonce, ts):
    """A stateless CSRF token: "<ts>.<nonce>.<sig>". nonce is caller-supplied
    ([A-Za-z0-9_-]); no server storage. TTL is enforced in verify_state."""
    nonce = nonce if _NONCE_RE.fullmatch(nonce or "") else "x"
    return f"{int(ts)}.{nonce}.{_sign_state(secret, ts, nonce)}"


def verify_state(secret, state, now, ttl=300):
    """True iff `state` is a well-formed, in-TTL, correctly-signed token."""
    if not state or not isinstance(state, str):
        return False
    parts = state.split(".")
    if len(parts) != 3:
        return False
    ts_s, nonce, sig = parts
    try:
        ts = int(ts_s)
    except ValueError:
        return False
    if not _NONCE_RE.fullmatch(nonce or ""):
        return False
    if not hmac.compare_digest(sig, _sign_state(secret, ts, nonce)):
        return False
    return 0 <= (int(now) - ts) <= int(ttl)


def state_nonce(state):
    """The nonce embedded in a well-formed state ("<ts>.<nonce>.<sig>"), or "".
    Call only AFTER verify_state has confirmed the state; this does not re-verify."""
    if not state or not isinstance(state, str):
        return ""
    parts = state.split(".")
    return parts[1] if len(parts) == 3 else ""


def parse_identity(user_json):
    """Lowercased Discord `username` from a /users/@me dict, or "" on anything
    unexpected. The relay passes the already-parsed JSON."""
    if not isinstance(user_json, dict):
        return ""
    return (user_json.get("username") or "").strip().lower()


def match_subject(username, discord_map):
    """Crew name whose Discord handle == username (case-insensitive), or None.
    discord_map is {handle_lower: crew_name} from CrewSource.discord_map()."""
    key = (username or "").strip().lower()
    if not key:
        return None
    return (discord_map or {}).get(key)


def valid_redirect_host(host):
    """True iff host is a bare MagicDNS name safe to build a redirect_uri from.
    It blocks a forged Host header injecting CR-LF or an off-tailnet redirect;
    Discord's exact registered-redirect match is the real guard."""
    return bool(host) and bool(_HOST_RE.fullmatch(host))
