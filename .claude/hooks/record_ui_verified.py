#!/usr/bin/env python3
"""Record that the given UI files have been visually verified.

Called as the final step of the `ui-visual-verification` skill, AFTER you have
rendered the changed surface and looked at the screenshot. Writes each file's
current content hash into runtime/ui-visual-verified.json; the Stop hook
ui_visual_verify_gate.py reads that marker and stops blocking once every changed
UI file's recorded hash matches its current content.

Usage:
    python3 .claude/hooks/record_ui_verified.py src/director/director-panel.html [more…]

Re-editing a file changes its hash and re-arms the gate, so run this after your
final edit. The marker lives under runtime/ (gitignored): local session state,
never committed.
"""
import hashlib
import json
import os
import sys

MARKER = os.path.join("runtime", "ui-visual-verified.json")


def _hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv):
    if not argv:
        sys.stderr.write("usage: record_ui_verified.py <ui-file> [<ui-file>…]\n")
        return 2
    try:
        with open(MARKER, encoding="utf-8") as fh:
            marker = json.load(fh)
        if not isinstance(marker, dict):
            marker = {}
    except (OSError, ValueError):
        marker = {}

    for path in argv:
        norm = path.replace("\\", "/")
        try:
            marker[norm] = _hash(path)
        except OSError as exc:
            sys.stderr.write(f"skip {path}: {exc}\n")
            continue
        print(f"verified {norm}")

    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
