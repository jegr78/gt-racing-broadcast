# `iro update` — self-updating standalone binary

**Date:** 2026-06-05
**Status:** approved design
**Branch:** `feat/self-update` (built in its own worktree; the main checkout
stays free for parallel work). Independent of, but designed to compose with,
the release-automation spec (`2026-06-05-release-automation-design.md`);
works equally with manually pushed tags.

## Problem

Operators download the binary once and never update it. `releases/latest`
always has the newest build, but the path "notice → download → extract →
replace" is manual friction. The binary should update itself.

## Solution

A new one-shot verb:

```
iro update            # check, confirm, download, self-replace
iro update --check    # check + report only, never touches anything
iro update --yes      # skip the confirmation prompt
```

## Components

### 1. `src/scripts/update.py` (new, one-shot pattern)

Registered in `ONESHOT_MAP` in `src/iro.py` as `"update": "scripts/update.py"`.
It needs **no** injected frozen flags (`_oneshot_extra` adds nothing for it):
downloads go to a tempdir, the swap targets `sys.executable`.

Flow:

1. **Dev guard.** Current version comes from `iro`'s `version()` (the bundled
   `VERSION` file; repo mode returns `dev`). On `dev`, refuse:
   "running from source — update with git pull." The verb is frozen-only.
   (`iro.py` passes its version to the module via a `--current vX.Y.Z`
   argument it injects on dispatch, keeping update.py self-contained.)
2. **Check.** GET
   `https://api.github.com/repos/jegr78/IRO_Broadcast_Setup/releases/latest`
   (urllib, explicit User-Agent, 15 s timeout, unauthenticated — public repo,
   60 req/h/IP is plenty). Parse `tag_name` + `assets[].name/browser_download_url`.
   Network failure → clean message, exit 1, never a traceback.
3. **Compare.** Pure semver compare `parse_version("v0.2.1") -> (0,2,1)` vs
   current. Equal/older → "up to date (vX.Y.Z)", exit 0.
4. **Asset selection.** Per platform: `win32 → iro-windows.zip`,
   `darwin → iro-macos.tar.gz`, else `iro-linux.tar.gz`.
   **Building-window case:** with the release-automation dispatch chain, the
   release exists ~10 minutes *before* its assets are uploaded. A newer
   release whose platform asset is missing → "vX.Y.Z is out but the binaries
   are still building — retry in a few minutes", exit 1. Never treat it as
   up-to-date and never download a wrong asset.
5. **Confirm.** Print `vCURRENT → vNEW` and prompt (`--yes`/`--check` skip;
   same `confirmed()` semantics as the installers).
6. **Download + extract** (a 5xx, connection error or a body cut off midway is retried, 3 attempts in all, 3 s/6 s apart; a 4xx or a refused HTTPS downgrade is final; a download that still fails exits with the reason and "the binary is unchanged" instead of a traceback, #1098) to a tempdir created next to the binary (same filesystem — the final rename stays atomic; a system tempdir can sit on another fs → EXDEV) (stdlib `zipfile`/`tarfile`); locate
   `iro`/`iro.exe` inside the archive. Extraction uses a **manual member
   check** (reject absolute paths and `..` components) — works identically on
   every supported Python (3.11–3.13) instead of relying on the 3.12+
   tarfile filter.
7. **Swap** (per-OS plan, pure function, unit-tested; execution side-effectful
   and untested like the installers):
   - **macOS/Linux:** `os.replace(new, sys.executable)` + ensure exec bit.
     Bonus: urllib downloads carry no quarantine attribute → **no** Gatekeeper
     prompt after update (smoother than the manual flow).
   - **Windows:** a running exe cannot be overwritten but **can be renamed**:
     rename `iro.exe` → `iro-old.exe`, move the new binary into place.
     `iro-old.exe` is cleaned up best-effort on the **next** start (small
     hook next to `ensure_env_file()` in `main()`; failure is silent — the
     file is harmless).
8. **Done.** "updated to vX.Y.Z — restart iro." The running process keeps
   executing the old image; no in-process restart attempted. `.env` and
   `runtime/` live next to the binary and are never touched.

### 2. `src/iro.py` (small changes)

- `ONESHOT_MAP` entry + usage line.
- Dispatch injects `--current <version()>` for the `update` verb.
- Startup hook: best-effort `iro-old.exe` cleanup (Windows leftover), silent
  on failure, no-op when absent / not frozen.

### 3. Tests (`tests/test_update.py`, stdlib, injected fetch — no network)

- `parse_version` / compare: ordering, equal, `v`-prefix, malformed → None.
- Dev guard: `--current dev` refuses.
- Asset name per platform (all three).
- Missing-asset (building-window) classification.
- Swap plan per OS (argv/step tuples, not executed).
- Safe-extraction filter rejects `../` members.
- Registered in CI automatically (run-tests discovers test files).

### 4. Docs

- Wiki `Set-up-the-broadcast-PC.md`: one sentence in step 1 ("update later
  with `iro update`").
- Wiki `Run-an-event.md`: pre-event checklist gains "run `iro update`" as an
  early step (before cookies/preflight).
- README + `If-something-goes-wrong.md`: one line each.

## Explicit decisions

- **No CI smoke test for `update`** — the binary smoke stays network-free
  (GitHub API calls in release builds = flakiness). Coverage is unit-level.
- **No checksum/signature verification** — the download comes from the same
  HTTPS endpoint that serves the manual flow; without code signing a checksum
  from the same origin adds no real integrity. Noted as future work
  (`SHA256SUMS` asset + verify) if signing ever lands.
- **No auto-check on other verbs** (preflight/status stay offline-fast and
  deterministic; user decision 2026-06-05).
- **No in-process restart** after swap.

## Acceptance

1. Frozen binary vOLD, newer release with assets → `iro update --check`
   reports the newer version and exits 0 without touching anything.
2. `iro update --yes` replaces the binary; next invocation reports the new
   version; `.env`/`runtime/` untouched; on Windows `iro-old.exe` disappears
   on the next start.
3. Release exists but platform asset missing → building-window message,
   exit 1, binary untouched.
4. `python3 src/iro.py update` (repo mode) → refuses with the git-pull hint.
5. Offline → clean one-line error, exit 1.
