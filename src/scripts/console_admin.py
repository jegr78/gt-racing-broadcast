"""Console revocation store (#191), mirroring chat_admin.py:
pure validation + atomic JSON writes, no side effects until validation passes.

State file: runtime/<profile>/console-versions.json == {"versions": {key: int}}.
This is the ONLY token state; everything else is derived from CONSOLE_SECRET.
Pulled on producer takeover (apply_pulled), exactly like chat_admin.apply_pulled."""
import json
import os
import re
import tempfile

_KEY_RE = re.compile(r"[a-z0-9-]+")


def validate_versions(payload):
    """{"versions": {streamer_key: int>=1}} -> the cleaned dict. Raises ValueError
    on any malformed shape (mirrors chat_admin.validate_payload)."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    versions = payload.get("versions")
    if not isinstance(versions, dict):
        raise ValueError("missing 'versions' object")
    out = {}
    for key, val in versions.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ValueError(f"bad streamer key: {key!r}")
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise ValueError(f"bad version for {key!r}: {val!r}")
        out[key] = val
    return out


def load_versions(path):
    """{key: version} from disk, or {} when missing/corrupt. Best-effort like
    chat_admin.load_messages: a bad file must never lock everyone out."""
    try:
        with open(path, encoding="utf-8") as fh:
            return validate_versions(json.load(fh))
    except (OSError, ValueError):
        return {}


def current_version(versions, key):
    """Current version for a streamer_key, defaulting to 1 when absent."""
    return versions.get(key, 1)


def write_versions(path, versions):
    """Atomically persist {key: version} as {"versions": {...}} (temp + replace).
    Uses mkstemp in the target directory so the rename is same-filesystem, and
    unlinks the temp file on any failure, mirroring chat_admin.write_messages."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"versions": versions}, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # cleanup is best-effort; re-raise the original write failure below
        raise


def bump_version(path, key):
    """Increment a streamer's version (revocation), persist, return the new value."""
    versions = load_versions(path)
    versions[key] = current_version(versions, key) + 1
    write_versions(path, versions)
    return versions[key]


def apply_pulled(path, payload):
    """Validate a pulled {"versions": {...}} then overwrite *path*; return the
    count. Raises ValueError before touching disk (takeover safety)."""
    versions = validate_versions(payload)
    write_versions(path, versions)
    return len(versions)


def console_link_discord_payload(console_url, league_name=""):
    """Discord webhook JSON announcing the shared /console launcher link,
    mirroring the relay's cockpit_submission_payload / discord_health_payload so
    every racecast post reads alike: it posts as "GT Racecast", the @here mention
    sits in top-level `content` (Discord ignores mentions inside an embed), the
    link rides in a titled embed, and a non-empty `league_name` shows as the embed
    footer. Pure, no I/O."""
    league = (league_name or "").strip()
    desc = ("Open the launcher and sign in with Discord or your personal "
            f"link:\n{console_url}")
    embed = {"title": "\U0001F399️ Crew Console", "description": desc,
             "color": 0x5865F2}
    if league:
        embed["footer"] = {"text": league}
    return {"username": "GT Racecast",
            "content": "@here",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [embed]}
