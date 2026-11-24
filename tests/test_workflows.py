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
ACTIONS_DIR = os.path.join(ROOT, ".github", "actions")
APT_ACTION = os.path.join(ACTIONS_DIR, "apt-install", "action.yml")

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


# An unquoted YAML scalar may not contain ": ": the parser reads the second
# colon as a new mapping key and the whole workflow fails to load.
_SCALAR_RE = re.compile(r"^(\s*)(?:-\s+)?([\w.-]+):[ \t]+(?!$)(.*)$")
_BLOCK_RE = re.compile(r"^(\s*)(?:-\s+)?[\w.-]+:[ \t]*[|>][-+0-9]*\s*$")


def _unquoted_scalars():
    """Yield (basename, line_no, key, value) for plain scalars, skipping block bodies."""
    for path in sorted(glob.glob(os.path.join(WF_DIR, "*.yml"))):
        block_indent = None
        with open(path, encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                line = raw.rstrip("\n")
                if block_indent is not None:
                    if not line.strip() or len(line) - len(line.lstrip()) > block_indent:
                        continue
                    block_indent = None
                if _BLOCK_RE.match(line):
                    block_indent = len(line) - len(line.lstrip())
                    continue
                m = _SCALAR_RE.match(line)
                if not m:
                    continue
                value = m.group(3).strip()
                if not value or value[0] in "\"'#|>&*[{":
                    continue
                yield os.path.basename(path), i, m.group(2), value


def t_workflow_downloads_retry_transient_server_errors():
    # #1097: one GitHub 504 on the pinned gitleaks download failed a required check.
    # The checksum still guards the artifact, so a bounded retry is safe.
    found = 0
    paths = sorted(glob.glob(os.path.join(WF_DIR, "*.yml")) + glob.glob(os.path.join(ACTIONS_DIR, "*", "action.yml")))
    for path in paths:
        name = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if re.search(r"(^|[\s;|&(])curl\s", line):
                    found += 1
                    assert "--retry" in line and "--retry-all-errors" in line, (name, line.strip())
    assert found, "no curl download found: update this guard"


def t_plain_scalars_have_no_bare_colon():
    offenders = [f"{base}:{ln} {key}: {val}"
                 for base, ln, key, val in _unquoted_scalars()
                 if ": " in val.split(" #")[0]]
    assert not offenders, ("an unquoted value containing ': ' breaks the YAML parser "
                           "and the workflow never runs; quote it or reword: "
                           + "; ".join(offenders))


def _workflow_steps(base):
    """Step bodies of one workflow, split on the step-level `- name:` / `- uses:` lines."""
    with open(os.path.join(WF_DIR, base), encoding="utf-8") as fh:
        return re.split(r"\n(?=      - (?:name|uses):)", fh.read())[1:]


def t_workflows_run_apt_only_through_the_shared_action():
    for base in sorted(os.listdir(WF_DIR)):
        if not base.endswith(".yml"):
            continue
        with open(os.path.join(WF_DIR, base), encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                assert not re.search(r"\bapt(-get)?\s+(update|install)\b", line), (
                    f"{base}:{i} runs apt directly and would use the runner's regional mirror; "
                    "use ./.github/actions/apt-install")


def t_the_apt_action_skips_the_regional_mirror_and_bounds_every_download():
    with open(APT_ACTION, encoding="utf-8") as fh:
        text = fh.read()
    mirrors = re.findall(r"https?://[\w.-]+\.ubuntu\.com/ubuntu/", text)
    assert mirrors[:2] == ["https://archive.ubuntu.com/ubuntu/", "http://azure.archive.ubuntu.com/ubuntu/"], (
        "first the primary Ubuntu archive, then one other official mirror: " + repr(mirrors))
    assert "ec2" not in text, "the throttled AWS regional mirror must not be in the path"
    network = [ln for ln in text.splitlines() if re.search(r"apt-get .*(\bupdate\b|--download-only)", ln)]
    assert len(network) == 2 and all("timeout --kill-after" in ln for ln in network), (
        "apt update and the download each run under coreutils timeout: " + repr(network))
    assert "install -y --no-download" in text, "the install itself must only use the downloaded cache"
    assert "::error::" in text, "a failed download must say so instead of being cancelled"


def t_every_apt_install_step_has_a_step_timeout():
    for base in ("ci.yml", "event-sim.yml"):
        steps = [body for body in _workflow_steps(base) if "uses: ./.github/actions/apt-install" in body]
        assert any(re.search(r"^\s+packages:\s*ffmpeg\s*$", body, re.M) for body in steps), (
            f"{base} installs ffmpeg through ./.github/actions/apt-install")
    for base in sorted(os.listdir(WF_DIR)):
        if base.endswith(".yml"):
            for body in _workflow_steps(base):
                if "uses: ./.github/actions/apt-install" in body:
                    m = re.search(r"^\s+timeout-minutes:\s*(\d+)\s*$", body, re.M)
                    assert m and int(m.group(1)) >= 10, (
                        f"{base}: an apt-install step needs timeout-minutes >= 10, above the action's own limits")


def _preview_step(name):
    with open(os.path.join(WF_DIR, "preview.yml"), encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("- name: " + name)
    end = text.find("\n      - name: ", start + 1)
    return text[start:end if end > 0 else len(text)]


def t_preview_publish_only_touches_preview_tags():
    step = _preview_step("(Re)create the rolling pre-release")
    guard = step.find('case "$TAG" in preview-*)')
    assert guard >= 0, "the publish step must refuse a tag outside preview-*"
    assert guard < step.index("gh release delete"), "the guard must run before any delete"


def t_preview_publish_waits_for_every_build():
    with open(os.path.join(WF_DIR, "preview.yml"), encoding="utf-8") as fh:
        text = fh.read()
    job = text[text.index("\n  publish:"):text.index("\n  comment:")]
    cond = next(line for line in job.splitlines() if line.strip().startswith("if:"))
    assert "needs.build.result == 'success'" in cond, \
        "a failed matrix build must keep the old pre-release, whose assets the PR comment links"


def t_preview_comment_lists_the_sender_assets():
    step = _preview_step("Post/refresh the download-links comment")
    for asset in ("racecast-sender-windows.exe", "racecast-sender-macos"):
        assert asset in step, f"the PR comment must link {asset}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
