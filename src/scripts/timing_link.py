"""The public live timing link of an event (#1128): a random id in the URL is the
only credential, separate from the drivers' invite. `event start` creates it, `event
stop` ends it, the producer renews it. Stored like the invite (driver_invite).

Beside it, the served path (#1161): which link the producer hands out, `hosted` (the
league's timing host) or `funnel`. A renewal keeps it, `clear` ends it with the link."""
import json
import os
import tempfile

import atomic_replace
import driver_invite

FILENAME = "timing-link.json"
SERVED_FILENAME = "timing-served.json"
PATHS = ("hosted", "funnel")
current = driver_invite.current
create = driver_invite.create
Cache = driver_invite.Cache


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def _served_path(link_path):
    return os.path.join(os.path.dirname(os.path.abspath(link_path)), SERVED_FILENAME)


def served(link_path):
    """The producer's served path, or None when never set (callers pick the default)."""
    try:
        with open(_served_path(link_path), encoding="utf-8") as fh:
            value = json.load(fh).get("served")
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in PATHS else None


def set_served(link_path, value):
    if value not in PATHS:
        raise ValueError(f"served path must be one of {PATHS}")
    target = _served_path(link_path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".timing-served-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"served": value}, fh)
        atomic_replace.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def clear(link_path):
    driver_invite.clear(link_path)
    try:
        atomic_replace.retry_windows_file_operation(lambda: os.remove(_served_path(link_path)))
    except FileNotFoundError:
        pass  # never switched
