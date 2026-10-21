"""Atomic local provider credentials paired with portable public targets."""

import atomic_replace
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

MAX_FILE_BYTES = 64 * 1024


class StorageError(ValueError):
    def __init__(self, message, durable=False):
        super().__init__(message)
        self.durable = durable


def write_atomic(path, body, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".audience-", suffix=".tmp", delete=False
        ) as output:
            temporary = output.name
            if private:
                os.chmod(temporary, 0o600)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        atomic_replace.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _raw(path):
    try:
        with Path(path).open("rb") as source:
            body = source.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("Saved connection is too large")
    return body


def _read(path):
    try:
        body = _raw(path)
        data = json.loads(body) if body is not None else None
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _encode(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True).encode(
        "utf-8"
    )


class ConnectionFiles:
    def __init__(self, public, secret, owner, writer=None):
        self.public = Path(public)
        self.secret = Path(secret)
        self.owner = owner
        self.writer = writer or write_atomic

    def read_public(self):
        data = _read(self.public)
        return (
            data
            if data and type(data.get("version")) is int and data["version"] == 1
            else None
        )

    def load(self):
        public, private = self.read_public(), _read(self.secret)
        if not public or not private or public.get("enabled") is not True:
            return None
        ident = public.get("connectionId")
        if (
            not isinstance(ident, str)
            or not re.fullmatch(r"[0-9a-f]{32}", ident)
            or type(private.get("version")) is not int
            or private["version"] != 1
            or private.get("connectionId") != ident
            or private.get("profile") != self.owner
            or not isinstance(private.get("credentials"), dict)
        ):
            return None
        return public, private["credentials"]

    def activate(self, public, credentials):
        if not isinstance(public, dict) or not isinstance(credentials, dict):
            raise StorageError("Invalid connection data")
        ident = uuid.uuid4().hex
        public = {**public, "version": 1, "connectionId": ident, "enabled": True}
        private = {
            "version": 1,
            "profile": self.owner,
            "connectionId": ident,
            "credentials": credentials,
        }
        try:
            public_bytes, private_bytes = _encode(public), _encode(private)
            previous = _raw(self.secret)
            self.writer(self.secret, private_bytes, private=True)
        except (OSError, ValueError, TypeError):
            raise StorageError("Could not save local connection credentials") from None
        try:
            self.writer(self.public, public_bytes)
        except (OSError, ValueError, TypeError):
            message = "Could not save selected targets; previous connection retained"
            try:
                if previous is None:
                    self.secret.unlink(missing_ok=True)
                else:
                    self.writer(self.secret, previous, private=True)
            except (OSError, ValueError, TypeError):
                message = "Could not save the connection; reconnect after restarting"
            raise StorageError(message) from None
        return public

    def disconnect(self, public=None):
        public = self.read_public() if public is None else public
        failed = []
        try:
            self.writer(
                self.public, _encode({**(public or {}), "version": 1, "enabled": False})
            )
        except (OSError, ValueError, TypeError):
            failed.append("targets")
        try:
            self.secret.unlink(missing_ok=True)
        except OSError:
            failed.append("credentials")
        if failed:
            durable = len(failed) == 1
            if not durable:
                message = "Could not save disconnection; restart may restore the connection. Fix storage permissions and retry."
            elif failed == ["credentials"]:
                message = "Connection disabled; saved credentials could not be removed. Retry cleanup."
            else:
                message = "Credentials removed; disabled target settings could not be saved. Retry cleanup."
            raise StorageError(message, durable=durable)
