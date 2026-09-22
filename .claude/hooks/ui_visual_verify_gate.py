#!/usr/bin/env python3
"""Stop hook: block completion while a changed UI surface is not visually verified.

The gap this closes: styling/alignment bugs (e.g. issue #397's unstyled duration
input) ship because a UI change is reasoned about but never *rendered and looked
at*. This gate fires when the agent tries to end its turn: if any rendered UI
surface changed on this branch/working tree and has NOT been recorded as visually
verified, it blocks with exit code 2 and a checklist.

"Verified" = the `ui-visual-verification` skill's procedure was run and its final
step recorded the file's current content hash into the marker
`runtime/ui-visual-verified.json`. Re-editing a verified file changes its hash, so
the marker goes stale and the gate blocks again: verification is per content, not
once-per-file-forever.

Reads the Stop hook JSON payload on stdin (unused fields ignored); writes the
block message to stderr and exits 2 to block, or exits 0 to allow the stop.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

# Rendered UI surfaces whose look must be eyeballed after a change. Matched against
# repo-relative POSIX paths. Keep in sync with the skill + CLAUDE.md.
UI_PATTERNS = [
    r"^src/director/.*\.html$",          # Director Panel
    r"^src/cockpit/.*\.html$",           # Commentator Cockpit
    r"^src/console/.*\.html$",           # Crew Console launcher / pages
    r"^src/racecontrol/.*\.html$",       # Race Control desk
    r"^src/obs/(hud|splitscreen)\.html$",  # relay-served overlay pages
    r"^src/ui/.*\.(html|css|js)$",       # Control Center
    r"^src/racecast_ui\.py$",            # Control Center launcher/window
    r"^profiles/[^/]+/overlay/.*\.css$",  # per-league overlay CSS
]
_UI_RE = [re.compile(p) for p in UI_PATTERNS]
MARKER = os.path.join("runtime", "ui-visual-verified.json")


def _git(args):
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=15
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _changed_ui_files():
    names = set()
    names.update(_git(["diff", "--name-only"]).split("\n"))          # unstaged
    names.update(_git(["diff", "--name-only", "--cached"]).split("\n"))  # staged
    # Committed on this branch (vs the merge-base with the default branch).
    base = ""
    for ref in ("origin/main", "main"):
        base = _git(["merge-base", "HEAD", ref]).strip()
        if base:
            break
    if base:
        names.update(_git(["diff", "--name-only", f"{base}..HEAD"]).split("\n"))
    return sorted(
        n for n in names if n and any(rx.match(n) for rx in _UI_RE)
    )


def _content_hash(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        # Deleted UI file: nothing to look at; treat as verified.
        return None


def _load_marker():
    try:
        with open(MARKER, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def main():
    try:
        json.load(sys.stdin)  # drain/parse the Stop payload; fields unused here
    except Exception:
        # Best-effort: a missing/malformed payload must not crash the gate.
        pass

    changed = _changed_ui_files()
    if not changed:
        sys.exit(0)

    marker = _load_marker()
    unverified = []
    for path in changed:
        cur = _content_hash(path)
        if cur is None:
            continue  # deleted
        if marker.get(path) != cur:
            unverified.append(path)

    if not unverified:
        sys.exit(0)

    lines = [
        "UI visual verification required before finishing.",
        "",
        "These rendered UI surfaces changed on this branch but have NOT been",
        "visually verified against their current content:",
    ]
    lines += [f"  - {p}" for p in unverified]
    lines += [
        "",
        "Run the `ui-visual-verification` skill: serve the page, take an",
        "ELEMENT screenshot of the changed component at real size, Read the PNG",
        "back, and confirm styling/alignment/spacing + focus & disabled states",
        "against the design system. Refresh the surface's wiki screenshot in the",
        "same change (CLAUDE.md hard rule).",
        "",
        "The skill's final step records each verified file's content hash into",
        f"{MARKER}. Until every changed UI file is recorded there, this gate blocks.",
        "If a listed file is a non-visual edit (comment/logic only), record it via",
        "the skill's marker step to acknowledge you judged it needs no render.",
    ]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
