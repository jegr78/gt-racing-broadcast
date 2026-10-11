"""Supported interpreter floor, usable before importing the operator CLI."""
import sys

MINIMUM = (3, 12)


def requirement(version=None):
    version = sys.version_info if version is None else version
    if tuple(version[:2]) < MINIMUM:
        return "Racecast requires Python 3.12 or newer. Install a supported Python or use the packaged Racecast binary."
    return None


def ensure_supported():
    message = requirement()
    if message:
        sys.exit(message)
