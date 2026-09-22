#!/usr/bin/env python3
"""Keep bundled, immutable files readable for the life of the process.

A PyInstaller onefile binary extracts the whole `src/` tree into the OS temp
directory and reads from there at runtime. macOS (dirhelper) and Linux
(systemd-tmpfiles) reap that directory on a schedule without checking whether a
process still has it in use. The age is per FILE and counted from last access,
so the interpreter's shared libraries survive because they stay mapped, while a
page that is only read when somebody opens it does not. A long-running Control
Center then answers every request with "file not found" although nothing crashed.

Reading such a file ONCE into memory removes the failure. That is only correct
for bundle content, which cannot change while we run. Anything meant to pick up
edits, such as a profile's overlay CSS, downloaded graphics or the Sheet cache,
must keep reading per request and must NOT go through this cache.
"""
import os


class BundleCache:
    """Read-through cache for immutable bundled files, keyed by path."""

    def __init__(self):
        self._data = {}

    def read(self, path):
        """Bytes of *path*, from memory once it has been read successfully.

        Raises OSError only when the file was never read AND is not on disk,
        which is a genuinely missing resource rather than an evicted one.
        """
        hit = self._data.get(path)
        if hit is not None:
            return hit
        with open(path, "rb") as fh:
            data = fh.read()
        self._data[path] = data
        return data

    def prewarm(self, paths):
        """Read *paths* now, while the extraction directory is still intact.

        Lazy caching alone would not help a file that nobody touches before the
        cleaner runs. Returns how many were loaded; a missing file is skipped
        rather than raised, so an optional page cannot break startup.
        """
        loaded = 0
        for path in paths:
            try:
                self.read(path)
            except OSError:
                continue
            loaded += 1
        return loaded

    def cached(self, path):
        return path in self._data


def eviction_hint(path):
    """Operator-facing message for a bundled file that is gone at runtime."""
    return (f"bundled file unavailable: {os.path.basename(path)}. The OS "
            "cleaned up this build's temporary extraction directory. "
            "Restart racecast to unpack it again.")
