"""Atomic file replacement that survives a concurrent reader on Windows.

On Windows, os.replace() onto a target another process holds open fails with
PermissionError [WinError 5], because Python opens files without
FILE_SHARE_DELETE. The relay reads the asset manifests and audience files
constantly, so a writer retries for a short, bounded time. Stdlib only: the
get-* downloaders import this indirectly through asset_metadata."""

import os
import time

ATTEMPTS = 10
FIRST_DELAY_S = 0.005
MAX_DELAY_S = 0.05


def replace(source, target, sleep=time.sleep):
    """os.replace(source, target), retrying a PermissionError up to ATTEMPTS
    times with a short backoff (about 0.3 s in total). Any other error, or the
    last PermissionError, propagates; the caller keeps its temp-file cleanup."""
    delay = FIRST_DELAY_S
    for attempt in range(1, ATTEMPTS + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == ATTEMPTS:
                raise
            sleep(delay)
            delay = min(delay * 2, MAX_DELAY_S)
