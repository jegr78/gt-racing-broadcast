#!/usr/bin/env python3
"""Edit-time portability guard (maintainer-only; not shipped).

Wired as a PostToolUse Edit|Write hook in .claude/settings.json. Blocks an edit
that bakes THIS machine's home directory into a repo file, which is the signal
that a machine-specific path leaked into committed code (CLAUDE.md: "Never
hardcode machine paths"; "Tests must run on any machine").

It deliberately flags only the *current* user's home (e.g. /Users/<me>), never
generic OS paths like /Applications/OBS.app or placeholder fixtures
(C:\\Users\\x\\...), so it does not fire on legitimate cross-platform code.

Reads the hook JSON from stdin; exit 2 + stderr blocks the edit, exit 0 allows."""
import json, os, sys

# Files worth scanning: source, docs and config that ship or get read by others.
_EXTS = (".py", ".md", ".json", ".html", ".css", ".txt", ".yml", ".yaml",
         ".companionconfig", ".env.example")


def _home_needles():
    """Machine-specific home-dir strings to reject (POSIX + the Windows form),
    only when they are real (a non-trivial home, not '/' or '')."""
    home = os.path.expanduser("~")
    if not home or home in ("/", "\\") or len(home) < 4:
        return []
    needles = {home}
    # also catch the path written with the other separator (mixed-OS code)
    needles.add(home.replace("/", "\\") if "/" in home else home.replace("\\", "/"))
    return [n for n in needles if n]


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # no parsable payload -> nothing to check, never block spuriously
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path or not path.endswith(_EXTS):
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return 0
    hits = [n for n in _home_needles() if n in text]
    if hits:
        sys.stderr.write(
            "portability guard: this file hardcodes a machine-specific path "
            f"({hits[0]}).\nUse os.path.expanduser('~'), a runtime-dir helper, or "
            "a fixture/placeholder instead; committed files must work on any "
            "machine (CLAUDE.md: never hardcode machine paths).\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
