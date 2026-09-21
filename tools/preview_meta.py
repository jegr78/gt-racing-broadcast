#!/usr/bin/env python3
"""Compute the identity (tag / version / title) of a preview build from the
triggering GitHub event. CI-only helper for .github/workflows/preview.yml, not
shipped. compute_preview_meta() is pure and unit-tested in tests/test_preview.py;
main() emits `key=value` lines for $GITHUB_OUTPUT.

Usage (from the workflow):
  python tools/preview_meta.py --event pull_request --pr 42 \
      --sha "$SHA" >> "$GITHUB_OUTPUT"
  python tools/preview_meta.py --event workflow_dispatch --ref main \
      --sha "$SHA" >> "$GITHUB_OUTPUT"
"""
import argparse
import re
import sys

PREVIEW_PREAMBLE = ("Automated preview build, not a release. Unsigned: expect a "
                    "one-time SmartScreen/Gatekeeper warning on first run.")

_RELEASE_PR_RE = re.compile(r"release\s+v?(\d+\.\d+\.\d+)")
_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.\d+")


def parse_release_pr_version(title):
    """Extract the X.Y.Z version from an open release-please PR title, e.g.
    'chore(main): release 1.1.0' -> '1.1.0' (a leading 'v' is tolerated). None
    when the title is empty or carries no release version."""
    m = _RELEASE_PR_RE.search(title or "")
    return m.group(1) if m else None


def next_minor(version):
    """Given a released version 'X.Y.Z' (or 'vX.Y.Z'), the probable next release
    if no release-please PR exists yet: 'X.(Y+1).0'. None if unparseable."""
    m = _SEMVER_RE.match((version or "").strip())
    if not m:
        return None
    return f"{int(m.group(1))}.{int(m.group(2)) + 1}.0"


def resolve_base_version(release_pr_title=None, latest_tag=None):
    """The probable next release version a preview targets:
      1. the version named in the open release-please PR title, else
      2. the next minor after the latest released tag, else
      3. None (the caller then omits the base from the preview identity).
    The gh/git lookups happen in the workflow and are passed in."""
    return parse_release_pr_version(release_pr_title) or next_minor(latest_tag)


def format_preview_notes(commits, sha, limit=50):
    """Markdown body for a preview pre-release: a bulleted Changes list of the
    commit subjects in `commits` (already most-recent-first) above the standing
    preview preamble and the build provenance. Blank lines are dropped; more than
    `limit` commits are truncated with an 'and N more' line. An empty list falls
    back to the provenance and preamble, so the release is never noteless."""
    short = (sha or "")[:7]
    clean = [c.strip() for c in commits if c and c.strip()]
    out = []
    if clean:
        shown = clean[:limit]
        out.append("### Changes")
        out.extend(f"- {c}" for c in shown)
        extra = len(clean) - len(shown)
        if extra > 0:
            out.append(f"- …and {extra} more commit{'' if extra == 1 else 's'}")
        out.append("")
    out.append(f"Built from commit `{short}`." if short else "Built from an unknown commit.")
    out.append("")
    out.append(PREVIEW_PREAMBLE)
    return "\n".join(out)


def _sanitize_ref(ref):
    """A git ref made safe for a tag suffix: drop a refs/heads/ or refs/tags/
    prefix and replace every '/' with '-' (feat/x -> feat-x). Empty -> 'main'."""
    ref = (ref or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    ref = ref.replace("/", "-")
    return ref or "main"


def compute_preview_meta(event_name, pr_number=None, ref=None, sha=None,
                         base_version=None):
    """Return {'tag', 'version', 'title'} for a preview build.

    PR builds are keyed by PR number (one rolling pre-release per open PR);
    dispatch builds are keyed by the sanitized ref (one per branch, e.g.
    preview-main). The version embeds the 7-char short SHA so `racecast --version`
    pinpoints the exact commit a tester is running.

    When `base_version` (the probable next release, e.g. '1.1.0') is known, it is
    placed first so the version is a valid SemVer prerelease
    ('1.1.0-preview.pr42.<sha>') and the title leads with it, letting a tester read
    which release base a preview targets even though the GitHub releases list sorts
    by build date. The tag is left unchanged: one stable tag per PR or ref,
    re-pointed on each push. When `base_version` is None the legacy identity is
    reproduced verbatim.
    """
    short = (sha or "")[:7]
    if event_name == "pull_request":
        if not pr_number:
            raise ValueError("pull_request event requires a PR number")
        n = int(pr_number)
        if base_version:
            return {
                "tag": f"preview-pr-{n}",
                "version": f"{base_version}-preview.pr{n}.{short}",
                "title": f"Preview {base_version} — PR #{n} ({short})",
            }
        return {
            "tag": f"preview-pr-{n}",
            "version": f"preview-pr{n}-{short}",
            "title": f"Preview: PR #{n} ({short})",
        }
    if event_name == "workflow_dispatch":
        r = _sanitize_ref(ref)
        if base_version:
            return {
                "tag": f"preview-{r}",
                "version": f"{base_version}-preview.{r}.{short}",
                "title": f"Preview {base_version} — {r} ({short})",
            }
        return {
            "tag": f"preview-{r}",
            "version": f"preview-{r}-{short}",
            "title": f"Preview: {r} ({short})",
        }
    raise ValueError(f"unsupported event: {event_name!r}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `notes` mode: read commit subjects from stdin, one per line, most-recent
    # first, as `git log --format=%s <range>` emits them.
    if argv and argv[0] == "notes":
        ap = argparse.ArgumentParser(prog="preview_meta.py notes")
        ap.add_argument("--sha", required=True)
        ap.add_argument("--limit", type=int, default=50)
        a = ap.parse_args(argv[1:])
        sys.stdout.write(format_preview_notes(sys.stdin.read().splitlines(),
                                              a.sha, a.limit) + "\n")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--pr")
    ap.add_argument("--ref")
    ap.add_argument("--sha", required=True)
    # Optional version inputs gathered by the workflow: the open release-please PR
    # title and the latest released tag. Either or both may be empty;
    # resolve_base_version() then falls back to the legacy identity.
    ap.add_argument("--release-pr-title", default="")
    ap.add_argument("--latest-tag", default="")
    a = ap.parse_args(argv)
    base_version = resolve_base_version(a.release_pr_title, a.latest_tag)
    meta = compute_preview_meta(a.event, a.pr, a.ref, a.sha, base_version)
    for key, value in meta.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
