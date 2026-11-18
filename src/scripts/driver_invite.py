"""The event invite link for driver telemetry and the codes it issues (#1111).

One invite per event: `event start` creates it, `event stop` clears it, and the
producer regenerates it. The file lives in the profile's runtime directory and the
relay reads it per request, so a change needs no relay restart.

A driver code names one registration: entry, optional stint or qualifying segment,
the driver's name and a random connection id. It is signed with a key that lives only
in the invite file, never in the link, so a driver cannot mint codes, and a
regenerated invite revokes every code. The `D1:` prefix and the single dot keep it
from ever parsing as a console token. Stdlib only.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time

import atomic_replace

FILENAME = "driver-invite.json"
PREFIX = "D1:"
MAX_CODE = 1024
NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,32}\Z")   # it ends up in a shell command line
MAX_ENTRY = 120


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def current(path):
    """The invite {id, created, key} or None; `id` is the public link part, `key`
    signs the codes and never leaves the machine."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) and len(doc[k]) >= 20
                                            for k in ("id", "key")):
        return None
    return {"id": doc["id"], "created": doc.get("created"), "key": doc["key"]}


def create(path, now=None):
    """Write a new invite, replacing any earlier one, and return it."""
    invite = {"id": secrets.token_urlsafe(18), "created": time.time() if now is None else now,
              "key": secrets.token_urlsafe(32)}
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".driver-invite-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(invite, fh)
        atomic_replace.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return invite


def clear(path):
    try:
        atomic_replace.retry_windows_file_operation(lambda: os.remove(path))
    except FileNotFoundError:
        pass  # no invite: nothing to revoke


class Cache:
    """`current(path)` re-read only when the file changes: the relay asks on every
    ingest post, 300 times a second with 15 senders."""

    def __init__(self, path):
        self.path = path
        self._stamp = self._invite = None

    def get(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            self._stamp = self._invite = None
            return None
        if stamp != self._stamp:
            self._invite, self._stamp = current(self.path), stamp
        return self._invite


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(key, payload):
    mac = hmac.new(key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return _b64(mac.digest()[:16])


def _problem(entry, name, segment):
    if not isinstance(name, str) or not NAME.match(name):
        return "the name takes up to 32 ASCII letters, digits, spaces, _ . -"
    if not isinstance(entry, str) or not entry.strip() or len(entry) > MAX_ENTRY:
        return "pick an entry"
    if segment is not None and (not isinstance(segment, str) or len(segment) > MAX_ENTRY):
        return "pick a stint or segment"
    return None


def issue(invite, entry, name, segment=None):
    """A signed code for one registration of `invite` (from `current`). Raises
    ValueError on a bad name, entry or segment."""
    problem = _problem(entry, name, segment)
    if problem:
        raise ValueError(problem)
    doc = {"e": entry, "s": segment, "n": name, "c": secrets.token_urlsafe(9)}
    payload = _b64(json.dumps(doc, separators=(",", ":")).encode("utf-8"))
    return PREFIX + payload + "." + _sign(invite["key"], payload)


def verify(invite, code):
    """{entry, segment, name, conn} for a valid code of this invite, else None."""
    if not invite or not isinstance(code, str) or len(code) > MAX_CODE or not code.isascii():
        return None
    if not code.startswith(PREFIX) or code.count(".") != 1:
        return None
    payload, sig = code[len(PREFIX):].split(".")
    if not payload or not hmac.compare_digest(sig.encode("ascii", "replace"),
                                              _sign(invite["key"], payload).encode("ascii")):
        return None
    try:
        doc = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict) or _problem(doc.get("e"), doc.get("n"), doc.get("s")):
        return None
    if not isinstance(doc.get("c"), str) or not 8 <= len(doc["c"]) <= 32:
        return None
    return {"entry": doc["e"], "segment": doc.get("s"), "name": doc["n"], "conn": doc["c"]}
