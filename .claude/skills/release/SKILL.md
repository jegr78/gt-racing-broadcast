---
name: release
description: Cut and publish a GT Racing Broadcast release end-to-end: local gates, release-please version control, Release PR merge, binary watch. Use when the user wants to release a version (e.g. "release 1.1.0", "cut the next release", "merge the release PR").
---

# GT Racing Broadcast Release

Drives the repo's release-please flow from local verification to published
binaries. The release pipeline and its failure modes are documented here so no
step is rediscovered by trial and error (see CLAUDE.md: pipeline problems are
research-first).

## How releases work here

- **release-please** keeps a standing Release PR on `main` (branch
  `release-please--branches--main`); merging it creates the `v*` tag.
- release-please tags via `GITHUB_TOKEN`, which **cannot** trigger on-tag
  workflows: `release-please.yml` therefore dispatches `release.yml`
  explicitly. A manually pushed `v*` tag also triggers `release.yml` directly.
- `release.yml` checks out **the tag**, runs the test suite, builds the
  PyInstaller binary per OS (`tools/build-binary.py --version <tag>`), and
  uploads **4 assets**: `racecast-windows.zip` / `racecast-macos.tar.gz` /
  `racecast-linux.tar.gz` / `racecast-linux-arm64.tar.gz` (the two Linux archives
  build on the `ubuntu-latest` + `ubuntu-24.04-arm` matrix runners): to the
  GitHub release (`--clobber`, create is idempotent).
- The version is derived from conventional commits. To force a specific
  version: empty commit with a `Release-As: X.Y.Z` footer on `main`.

## Steps

1. **Local gates** (all must be green before touching the release):
   ```bash
   python3 tools/run-tests.py
   python3 tools/lint.py
   python3 tools/build.py
   python3 tools/build-binary.py --version local-check   # same step release.yml runs
   ```
   The last one is the step that broke v1.0.0 (stale CLI flag only exercised at
   release time): never skip it.

2. **State check:** `git status` clean, `main` synced with origin,
   `gh pr list` → note the standing Release PR number and its current version.

3. **Version control:** if the PR's version is not the wanted one, push an
   empty commit (needs user-approved push to main):
   ```bash
   git commit --allow-empty -m "chore: release X.Y.Z

   Release-As: X.Y.Z"
   ```
   Then wait for the `release-please` workflow run and confirm the PR title
   flipped to `chore(main): release X.Y.Z` (files: `.release-please-manifest.json`,
   `CHANGELOG.md`, `version.txt`).

4. **Merge gate:** CI on `main` green (`gh run list --workflow=ci.yml`)?
   Then, with explicit user OK, `gh pr merge <n> --squash`.

5. **Watch the pipeline** (background until-loop, not polling chat):
   release done when `gh release view vX.Y.Z` shows **4 assets**; abort-signal
   is a failed `release.yml` run. Builds take ~5–15 min.

6. **If a build job fails:** read `--log-failed` for the failing *step* first,
   fix on `main`, verify with the local `build-binary` gate, then re-point the
   tag with `git tag -f vX.Y.Z <fix-sha> && git push origin vX.Y.Z --force`,
   **only with explicit user approval** (it rewrites a published ref; safe
   while the release has no assets/consumers).

7. **Post-release:** check open CodeQL alerts (`gh api .../code-scanning/alerts
   --jq '[.[] | select(.state=="open")] | length'`), sync the wiki if release
   notes/docs changed (`python3 tools/sync-wiki.py --dry-run` → user OK →
   publish), and confirm `iro update` would see the new version.

## Known failure points

- **Stale required-check contexts** in branch protection block merges after CI
  job renames: fix the ruleset, don't bypass.
- **Tag builds, not main builds:** a fix pushed to `main` after tagging does
  NOT reach the release until the tag moves.
- **SmartScreen/Gatekeeper:** unsigned binaries warn on first run: expected,
  documented, not a release blocker.
