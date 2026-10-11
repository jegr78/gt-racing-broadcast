"""Profile-owned opt-in and explicit recording grants, independent of Console identity."""

import json
import re
import threading
from pathlib import Path

import gt7_context
import gt7_recording

MAX_GRANTS = 128
MAX_RECORDINGS = 4096
_LOCK = threading.RLock()


class AccessError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _identity(value):
    return isinstance(value, str) and re.fullmatch(
        r"[0-9a-f]{32}(?:[0-9a-f]{32})?", value
    )


def _label(value):
    if value is None:
        return "Shared telemetry reference"
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 160
        or any(ord(c) < 32 for c in value)
    ):
        raise AccessError(
            "invalid_settings",
            "Reference label must be readable text, at most 160 characters",
        )
    return value.strip()


class Store:
    def __init__(self, profile_runtime, available=False):
        self.root = Path(profile_runtime).resolve()
        self.recdir = self.root / "telemetry-recordings"
        self.path = self.root / "race-engineer-access.json"
        self.available = bool(available)

    def read(self):
        if not self.path.exists():
            return dict(version=1, revision=0, enabled=False, grants={})
        try:
            with self.path.open(encoding="utf8") as f:
                raw = f.read(131073)
            if len(raw) > 131072:
                raise ValueError("oversized")
            doc = json.loads(raw)
            if (
                not isinstance(doc, dict)
                or set(doc) != {"version", "revision", "enabled", "grants"}
                or type(doc["version"]) is not int
                or doc["version"] != 1
            ):
                raise ValueError("schema")
            if (
                type(doc["revision"]) is not int
                or doc["revision"] < 0
                or type(doc["enabled"]) is not bool
            ):
                raise ValueError("types")
            if not isinstance(doc["grants"], dict) or len(doc["grants"]) > MAX_GRANTS:
                raise ValueError("grants")
            for ident, grant in doc["grants"].items():
                if (
                    not _identity(ident)
                    or not isinstance(grant, dict)
                    or set(grant) != {"name", "signature", "label"}
                ):
                    raise ValueError("grant")
                self._path(grant["name"])
                if not isinstance(grant["label"], str):
                    raise ValueError("label")
                _label(grant["label"])
                if (
                    not isinstance(grant["signature"], list)
                    or len(grant["signature"]) != 4
                    or any(type(v) is not int for v in grant["signature"])
                ):
                    raise ValueError("signature")
            return doc
        except (ValueError, OSError, TypeError, RecursionError) as exc:
            raise AccessError(
                "invalid_settings",
                "Engineer access settings are unreadable; repair the profile settings locally",
            ) from exc

    def _path(self, name):
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or "\\" in name
            or not name.endswith(".gt7rec")
        ):
            raise AccessError(
                "invalid_reference", "Only finalized profile recordings may be shared"
            )
        path = self.recdir / name
        if (
            self.recdir.is_symlink()
            or path.is_symlink()
            or path.parent.resolve() != self.recdir
            or not path.resolve().is_relative_to(self.root)
        ):
            raise AccessError("invalid_reference", "Recording is outside this profile")
        return path

    def _source(self, name):
        path = self._path(name)
        try:
            before = path.stat()
            ident = gt7_context.source_identity(str(path))
            after = path.stat()
            signature = [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns]
            if signature != [
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ]:
                raise ValueError("changed")
            return ident, signature, path
        except (OSError, ValueError, gt7_recording.RecordingError) as exc:
            raise AccessError(
                "reference_unavailable",
                "Shared recording is unavailable or changed; ask the producer to review its grant",
            ) from exc

    def recordings(self):
        """Producer-only bounded header inventory; never a Console archive listing."""
        if not self.available:
            return []
        out = []
        for scanned, path in enumerate(self.recdir.glob("*.gt7rec")):
            if scanned >= MAX_RECORDINGS:
                raise AccessError(
                    "archive_limit",
                    "Reduce the profile archive before managing Engineer grants",
                )
            try:
                ident, _sig, _path = self._source(path.name)
            except AccessError:
                continue
            out.append(dict(id=ident, name=path.name))
        return out

    def update(self, enabled=None, grant=None, revoke=None, label=None, expected=None):
        if not self.available:
            raise AccessError(
                "source_unavailable",
                "Race Engineer is available only for a solo POV telemetry profile",
            )
        if enabled is not None and type(enabled) is not bool:
            raise AccessError("invalid_settings", "Enablement must be a boolean")
        if (grant is not None and not _identity(grant)) or (
            revoke is not None and not _identity(revoke)
        ):
            raise AccessError(
                "invalid_reference", "Use a stable recording identity from this profile"
            )
        if grant is not None and revoke is not None:
            raise AccessError(
                "invalid_settings", "Grant or revoke one reference per change"
            )
        try:
            with _LOCK, gt7_context._file_lock(str(self.path)):
                doc = self.read()
                if type(expected) is not int or expected != doc["revision"]:
                    raise AccessError(
                        "conflict", "Access settings changed; reload before saving"
                    )
                if enabled is not None:
                    doc["enabled"] = enabled
                if grant is not None:
                    matches = [r for r in self.recordings() if r["id"] == grant]
                    if len(matches) != 1:
                        raise AccessError(
                            "reference_unavailable",
                            "Recording identity is missing or ambiguous in this profile",
                        )
                    ident, signature, _path = self._source(matches[0]["name"])
                    if ident != grant:
                        raise AccessError(
                            "reference_unavailable", "Recording changed before sharing"
                        )
                    doc["grants"][grant] = dict(
                        name=matches[0]["name"],
                        signature=signature,
                        label=_label(label),
                    )
                    if len(doc["grants"]) > MAX_GRANTS:
                        raise AccessError(
                            "grant_limit", "Revoke older references before sharing more"
                        )
                if revoke is not None:
                    doc["grants"].pop(revoke, None)
                doc["revision"] += 1
                gt7_context._atomic(str(self.path), doc)
                return doc
        except gt7_context.Conflict as exc:
            raise AccessError(
                "conflict", "Access settings are busy; retry explicitly"
            ) from exc

    def _reference(self, ident, doc):
        if not _identity(ident):
            raise AccessError("invalid_reference", "Invalid reference identity")
        grant = doc["grants"].get(ident)
        if grant is None:
            raise AccessError(
                "reference_unavailable", "Reference grant was revoked or does not exist"
            )
        path = self._path(grant["name"])
        try:
            st = path.stat()
        except OSError as exc:
            raise AccessError(
                "reference_unavailable", "Shared recording is unavailable"
            ) from exc
        if [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns] != grant["signature"]:
            raise AccessError(
                "reference_unavailable",
                "Shared recording changed; producer review is required",
            )
        actual, signature, path = self._source(grant["name"])
        if actual != ident or signature != grant["signature"]:
            raise AccessError(
                "reference_unavailable",
                "Shared recording changed; producer review is required",
            )
        return path

    def _authorized_doc(self, roles):
        if "race_engineer" not in roles:
            raise AccessError("forbidden", "Race Engineer capability is required")
        if not self.available:
            raise AccessError(
                "source_unavailable", "Race Engineer is unavailable for this source"
            )
        doc = self.read()
        if not doc["enabled"]:
            raise AccessError(
                "feature_disabled", "The producer has disabled Race Engineer access"
            )
        return doc

    def authorize(self, roles):
        doc = self._authorized_doc(roles)
        refs = []
        for ident, grant in doc["grants"].items():
            try:
                self._reference(ident, doc)
            except AccessError:
                continue
            refs.append(dict(id=ident, label=grant["label"], context_shared=True))
        return dict(version=1, enabled=True, revision=doc["revision"], references=refs)

    def reference(self, ident, roles):
        return self._reference(ident, self._authorized_doc(roles))
