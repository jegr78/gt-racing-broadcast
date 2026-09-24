---
name: ship-feature
description: "Take a feature or GitHub issue from understanding to a merged PR the way this repo expects: branch, TDD, local gates, one PR per issue, green CI, squash-merge. Use when asked to \"implement this issue\", \"fix these issues as PRs\", \"build and ship feature X\", or \"address the open issues\"."
---

# Ship a feature / issue end-to-end

Drives one change from understanding to a merged PR on `main`, with the repo's
gates baked in so nothing is rediscovered each time. `main` is protected
(PR-only); pushing and merging are **outward-facing on a PUBLIC repo**: confirm
with the user before the first push and before each merge. For cutting a
versioned release afterwards, use the **release** skill (don't reinvent it here).

## Before anything

- `git fetch origin` and check `origin/main`, never branch off a stale main
  (CLAUDE.md). One branch + one PR **per issue**, never two issues in one PR
  (release-please parses the squash commit subject, so the PR title must be a
  single conventional commit: `fix(scope): …` or `feat(scope): …`).
- Read the issue in full (`gh issue view N`) and the code it touches before
  designing. For anything non-trivial, brainstorm/spec the approach first; ask
  the user on real gray areas instead of inventing rules or assumptions
  (CLAUDE.md: never invent domain rules; state assumptions explicitly).

## The loop (per issue)

1. **Branch** off fresh main: `git checkout -b fix/<slug>` (or `feat/<slug>`).
2. **TDD**: failing test first, then the implementation (CLAUDE.md). Edit only
   under `src/`. Mind the hard rules the edit-time hooks also enforce: no
   machine paths / real IPs in committed files (Tailscale test IPs are
   `100.64.0.0/10`), and build fixed-OS paths with forward slashes, not
   `os.path.join` (the Windows runner injects backslashes: see CLAUDE.md
   cross-platform rule).
3. **Local gates, all green before a PR** (this is the closest mirror of CI):
   ```bash
   python3 tools/run-tests.py     # whole suite (what CI runs)
   python3 tools/lint.py          # ruff (also runs on every edit via the hook)
   python3 tools/build.py         # must exit 0: its verify step ~= CI
   ```
   For a relay change also run `python3 tests/test_pov.py`. When practical,
   verify the real behavior (run the CLI / probe the live system) rather than
   trusting tests alone.
4. **Commit** atomically with a conventional message; end the body with the
   `Co-Authored-By:` trailer (see the repo/global convention).
5. **Push + PR** (after user OK): `git push -u origin <branch>`, then
   `gh pr create --base main --title "<conventional title>" --body "… Closes #N …"`.
   PR body ends with the `🤖 Generated with [Claude Code]` line.

## Land it

6. **Wait for green CI**: poll `gh pr checks <PR>`; the matrix is macOS +
   **Windows** + Linux × Python 3.11–3.13, plus lint / CodeQL / gitleaks /
   binary-smoke. A Windows-only failure is usually a path-separator or
   machine-specific bug (CLAUDE.md cross-platform rule); fix it, push, re-poll.
   Do **not** merge on red.
7. **Merge** (after user OK): `gh pr merge <PR> --squash --delete-branch`.
8. **Multiple PRs at once:** merge them one at a time. After the first lands,
   a second PR touching the same files goes `BEHIND`: integrate main into it
   locally (`git merge origin/main`), re-run the local gates to confirm the
   merged result is correct (not just textually clean), push, wait for CI, then
   merge. Don't trust GitHub's "MERGEABLE" alone when files overlap.
9. **Wrap up:** `git checkout main && git pull`, confirm the issue auto-closed,
   prune merged branches, and report what shipped.

## Checklist

- [ ] Fresh main; one branch/PR per issue; conventional title
- [ ] Failing test first, then fix; edits only under `src/`
- [ ] No machine paths / real IPs; cross-platform paths handled
- [ ] `run-tests.py` + `lint.py` + `build.py` (exit 0) all green locally
- [ ] User OK before push; PR body has `Closes #N`
- [ ] CI green across the full matrix (incl. Windows) before merge
- [ ] User OK before merge; squash + delete branch; issue closed; branches pruned
