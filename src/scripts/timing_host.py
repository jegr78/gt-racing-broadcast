"""The publish protocol between the relay and the league's timing host (#1161).

The relay holds one WebSocket to `/t/<link id>/publish` and sends a `state` frame per
tick; the host fans it out to every viewer and acks. Spec:
docs/superpowers/specs/2026-11-17-timing-host-fanout-design.md. Stdlib only; the relay
imports it.
"""
import base64
import collections
import json
import re
import unicodedata

PROTOCOL_V = 1
LINK_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
SCRIPT_PREFIX = "racecast-timing-"

Settings = collections.namedtuple("Settings", "url key")


def script_name(profile):
    """The Worker script name for a league: lowercase, [a-z0-9-], at most 63 chars."""
    ascii_name = unicodedata.normalize("NFKD", profile).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return SCRIPT_PREFIX + (slug[:47].rstrip("-") or "league")


def _host(host_url):
    return host_url.strip().rstrip("/").split("://", 1)[-1]


def publish_url(host_url, link_id):
    return f"wss://{_host(host_url)}/t/{link_id}/publish?v={PROTOCOL_V}"


def viewer_url(host_url, link_id):
    return f"https://{_host(host_url)}/t/{link_id}"


def state_frame(seq, age_s, body, fallback):
    """A state frame around the already encoded public JSON `body` (bytes)."""
    head = json.dumps({"t": "state", "seq": seq, "age_s": round(age_s, 1), "fallback": fallback})
    return head[:-1] + ', "data": ' + body.decode("utf-8") + "}"


def brand_frame(key, png):
    """One brand logo for the room to store and push to its viewers over their socket."""
    return json.dumps({"t": "brand", "key": key, "png": base64.b64encode(png).decode("ascii")})


def end_frame():
    return json.dumps({"t": "end"})


def moved_frame(to):
    return json.dumps({"t": "moved", "to": to})


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def parse_host_frame(text):
    """The host's ack {t, seq, viewers, requests_today}, or None for anything else."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("t") != "ack":
        return None
    keys = ("seq", "viewers", "requests_today")
    if not all(_count(doc.get(k)) for k in keys):
        return None
    return {"t": "ack", **{k: doc[k] for k in keys}}


def _parse_env(text):
    """config.parse_env_text's rules; the relay may not import config.py."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def read_settings(profile_env_path):
    """Host URL and publish key from the league's profile.env, None unless both are set."""
    if not profile_env_path:
        return None
    try:
        with open(profile_env_path, encoding="utf-8") as fh:
            env = _parse_env(fh.read())
    except OSError:
        return None
    url, key = env.get("TIMING_HOST_URL", ""), env.get("TIMING_HOST_PUBLISH_KEY", "")
    return Settings(url, key) if url and key else None
