#!/usr/bin/env python3
"""Stdlib checks that every third-party GitHub Actions step is pinned to a full
commit SHA and that gitleaks is pinned to a version plus checksum rather than
'latest'. A moving tag can be repointed upstream and would then run in CI with
the job's token. (#100)
Run: python3 tests/test_workflows.py"""
import glob, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WF_DIR = os.path.join(ROOT, ".github", "workflows")

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)")
_SHA_RE = re.compile(r"@[0-9a-f]{40}$")


def _uses_lines():
    """Yield (workflow_basename, line_no, ref) for every `uses:` in the workflows,
    skipping local (./) action references (which are never SHA-pinned)."""
    for path in sorted(glob.glob(os.path.join(WF_DIR, "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                m = _USES_RE.match(line)
                if not m:
                    continue
                ref = m.group(1)
                if ref.startswith("./") or ref.startswith("."):
                    continue
                yield os.path.basename(path), i, ref, line


def t_all_third_party_actions_are_sha_pinned():
    offenders = [f"{base}:{ln} {ref}"
                 for base, ln, ref, _line in _uses_lines()
                 if not _SHA_RE.search(ref)]
    assert not offenders, "un-pinned actions (use a full commit SHA): " + ", ".join(offenders)


def t_pinned_actions_keep_a_version_comment():
    # A SHA is opaque, so the trailing `# vX` comment is how a human and
    # Dependabot track which version it represents.
    missing = [f"{base}:{ln} {ref}"
               for base, ln, ref, line in _uses_lines()
               if _SHA_RE.search(ref) and "#" not in line.split(ref, 1)[1]]
    assert not missing, "SHA-pinned actions missing a '# version' comment: " + ", ".join(missing)


def t_gitleaks_is_pinned_with_checksum():
    with open(os.path.join(WF_DIR, "gitleaks.yml"), encoding="utf-8") as fh:
        text = fh.read()
    assert "releases/latest" not in text, "gitleaks must not install a moving 'latest'"
    assert re.search(r"[0-9a-f]{64}", text), "gitleaks download must be checksum-verified (sha256)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
