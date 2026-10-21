"""Atomic file replacement that survives a concurrent reader on Windows.

On Windows, os.replace() onto a target another process holds open fails with
PermissionError [WinError 5] (or a sharing violation, 32/33), because Python opens
files without FILE_SHARE_DELETE. The relay reads the asset manifests, audience
files and Race Engineer/context files constantly, so a writer retries for a short,
bounded time (#877). Stdlib only: the get-* downloaders import this indirectly
through asset_metadata."""

import os
import time

DEADLINE_S = 2.0
FIRST_DELAY_S = 0.01
MAX_DELAY_S = 0.1
# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
TRANSIENT_WINERRORS = (5, 32, 33)


def _on_windows():
    """The platform check, a seam so tests can simulate Windows without patching
    os.name globally (which turns every pathlib.Path into an unusable WindowsPath)."""
    return os.name == "nt"


def retry_windows_file_operation(operation):
    """Run `operation`, retrying a transient Windows file-access conflict until
    DEADLINE_S. Any other error, any error off Windows, or a conflict that outlasts
    the deadline propagates unchanged."""
    deadline = time.monotonic() + DEADLINE_S
    delay = FIRST_DELAY_S
    while True:
        try:
            return operation()
        except OSError as exc:
            if not _on_windows() or getattr(exc, "winerror", None) not in TRANSIENT_WINERRORS:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, MAX_DELAY_S)


def replace(source, target):
    """os.replace(source, target) through retry_windows_file_operation; the caller
    keeps its temp-file cleanup."""
    retry_windows_file_operation(lambda: os.replace(source, target))
