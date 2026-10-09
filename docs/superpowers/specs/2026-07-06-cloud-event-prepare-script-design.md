# Cloud event-preparation script — design

**Date:** 2026-07-06
**Status:** design (approved for spec review)
**Related:** #395 (cloud-producer spike), the provisioning design
`docs/superpowers/specs/2026-07-03-gpu-box-provisioning-design.md`, the runbook
`docs/superpowers/specs/2026-07-02-cloud-producer-spike-runbook.md` (Appendix A),
`tools/cloud/provision.py`, `tools/cloud/README.md`, wiki `Cloud-Producer.md`.

## Context & goal

`provision.py` brings a fresh GCP GPU box to "ready to onboard a league" (the **machine
layer**). The recurring, **per-event** preparation on top of it — refresh the tool, pick the
league, refresh cookies/graphics/media/brands, sanity-check the box — is today only prose in
`Cloud-Producer.md` §4 and `Run-an-event.md` "Before you go live". This spec turns that prose
into a second, standalone script so the operator runs one command per event instead of
hand-typing the sequence, and so nothing is forgotten.

**Two independent scripts, one hand-off:**

- `tools/cloud/provision.py` — existing machine-layer script. Its only change here: at the end
  it **copies `prepare-event.py` into the box** (`/home/racecast/`, racecast-owned, `+x`), so
  the event script is present without a second upload.
- `tools/cloud/prepare-event.py` — new **on-box** event-prep script, run by the operator over
  SSH as the `racecast` user.

Instance lifecycle (does the box exist? start it vs. create+provision) stays a **manual,
documented `gcloud` step** — not a script. That is the operator's judgement call and brackets
the two scripts; it is not part of either.

## Scope boundary (decided)

`prepare-event.py` covers the on-box, per-event preparation and **stops at "ready"**. It does
**not** go live: no `racecast event start`, no OBS stream key, no broadcast Part. Go-live stays
a deliberate, separate operator/director action (Control Center "Start event" or
`racecast event start`). This matches the operator's step list (which omitted `event start`).

## Non-goals

- No instance lifecycle (`gcloud instances create/start/stop`) — documented manual step.
- No one-time-per-league GUI/interactive setup (OBS scene-collection import, RustDesk
  password, Firefox login, Discord OAuth consent, first Tailscale browser-join). The script
  **cannot** perform these over SSH; it **detects and instructs** instead (see §Readiness).
- No change to the shipped `racecast` CLI. `relay stop` gains no `--force` flag — the "fresh
  relay" guarantee uses the existing `freeport --force` (see §Sequence, step 8).
- Not a shipped artifact. Like `provision.py`, it is maintainer cloud glue under
  `tools/cloud/` (outside `dist/`, so the "no `.sh` shipped" build check does not apply).
  English-only per the repo rule.

## CLI surface

```
python3 prepare-event.py <league> [--no-twitch] [--no-speedtest] [--no-update]
```

- `<league>` (**required**) — the racecast profile name for this event. If omitted, or not
  present in `racecast profile list`, the script **aborts** with a clear pointer to the
  per-league onboarding (`racecast profile import …`; `tools/cloud/README.md` §4). This is the
  operator's "if newly provisioned, finish the manual setup first" case, surfaced as a hard,
  actionable error rather than a hidden branch — and it re-runs cleanly once fixed.
- `--no-twitch` — skip the Twitch cookie/auth refresh. **Default: run it** alongside YouTube
  (`racecast cookies twitch firefox`), so a league with gated Twitch feeds is prepared without
  a flag; a league that never uses Twitch can opt out to avoid the extra step.
- `--no-speedtest` — skip the bandwidth test (default: run it).
- `--no-update` — skip the binary self-update entirely (default: run it, with the preview
  guard below).

Intended to be run in an **interactive SSH session** (the operator logs in, then runs it), so
the preview prompt and readiness output have a TTY. A non-interactive invocation
(`ssh box --command="python3 prepare-event.py …"`, no TTY) still works but takes the safe defaults
(see the update logic).

## Form & error philosophy

- stdlib Python 3, first written in bash with `set -uo pipefail` (**not** `-e`: individual best-effort steps must be able to fail
  without aborting the whole run — the readiness report at the end is the gate, not a mid-run
  `set -e` trip). English-only.
- Runs **as `racecast`** on the box (not root). A guard checks this and the presence of the
  `racecast` binary on `PATH` before doing anything.
- **Hard-fail steps** (abort with guidance): the `racecast`-user/`PATH` guard, `<league>`
  present & imported, `profile use`, and a failed `racecast update` when one was attempted.
- **Soft steps** (warn, collect, continue): cookies, graphics, media, brands, speedtest. A
  transient network hiccup on an asset refresh must not block preparation — the gap resurfaces
  in the readiness report / preflight anyway, and these steps are all safe to re-run.
- `relay stop` / `freeport --force`: errors ignored (nothing running == success).

## Step sequence

Ordering note — **one deliberate deviation from the operator's literal list**: `profile use`
runs **before** graphics/media/brands, because those write into `runtime/<profile>/…` and are
profile-scoped; they must know the active league first. Cookies are machine-global (shared
`runtime/yt-cookies.txt`), so their position is not order-sensitive.

0. **Sanity guard.** Running as `racecast`? `racecast` on `PATH`? `<league>` given and in
   `racecast profile list`? Any no → abort with the exact fix.
1. **`racecast update`** — with the **preview guard** (see below). `--no-update` skips.
2. **`racecast profile use <league>`** — hard-fail on error.
3. **Cookies (soft):** `racecast cookies firefox` (YouTube) **and**
   `racecast cookies twitch firefox` (Twitch), both by default. `--no-twitch` skips the Twitch
   refresh. Both are soft — a missing signed-in session on either platform warns and continues.
4. **`racecast graphics`** (soft).
5. **`racecast media`** (soft).
6. **`racecast brands`** (soft).
7. **`racecast speedtest`** (soft; `--no-speedtest` skips). Runs before preflight so preflight
   can read its logged result against the 25/10 Mbps floor.
8. **Fresh relay:** `racecast relay stop` (graceful; no-op if not running) **then**
   `racecast freeport --force` (per-process kill of any orphaned holder of feed ports
   53001–53003; `--force` overrides the "relay/streams running" guard). This is the toolkit's
   real "force a clean state" mechanism — `relay stop` has no `--force` — so `event start`
   later binds fresh feed ports.
9. **`racecast preflight`** — surfaces any red hardware/tool item.

Then the **readiness report** (§ below), then a final line pointing at go-live
(*"Ready → go live via Control Center 'Start event' or `racecast event start`."*). The script
starts nothing.

## `racecast update` — preview guard

The cloud box currently runs a deliberate **`preview-main`** build (it carries the Linux fixes
— streamlink venv ≥ 8.2.0 + obs-pipewire-audio — that are on `main` but not yet in a stable
release). `racecast update` always installs **latest stable** (never a pre-release), so a blind
update would *downgrade off the preview and re-break* streamlink/audio. Hence:

- **Detect preview:** `racecast --version` contains the substring `preview`
  (preview version strings are `…-preview.main.<sha>` / `preview-main-<sha>`; a stable build is
  a clean `X.Y.Z`).
- **Preview installed → prompt** (TTY): *"Preview build `<cur>` installed (kept for the Linux
  fixes). Update to latest stable `<new>`? [y/N]"*. Default **No** (Enter keeps the preview);
  only an explicit `y` runs `racecast update`.
- **Stable installed → update directly** (`racecast update`): a no-op when already latest, an
  upgrade when a newer stable exists.
- **No TTY (non-interactive):** cannot prompt → **keep the preview** (safe) and print a note;
  a stable build still auto-updates.
- **`--no-update`:** skip the whole step regardless.

## Readiness report (the "ready" gate)

After preflight, print a green/red block — mirroring `provision.py`'s verification block — for
the **one-time-per-league manual items** that neither script can perform, each with the exact
fix command. This folds the operator's "if newly provisioned, finish the manual setup" idea
into a detect-and-instruct report that is also useful on every re-run:

| Check | How | Red → instruction |
|---|---|---|
| Tailnet joined | `racecast tailscale status` shows a `100.x` addr | `sudo tailscale up --ssh --hostname racecast-box` |
| OBS collection localized for this league | `runtime/<league>/GT_Endurance.import.json` exists | `racecast setup`, then import into OBS over RustDesk (once per league) |
| Feed cookies present | `runtime/yt-cookies.txt` exists & non-empty | sign in to YouTube in the box's Firefox (§6), then re-run |
| Discord voice token cached (only if the league uses Discord) | token cache present | `racecast discord join` once over RustDesk |
| Preflight | step 9 exit / output | the red preflight lines |

**Exit code:** non-zero when a **go-live prerequisite** is missing — specifically the tailnet
join or the localized OBS collection — so the operator cannot miss it. Soft-step warnings and
"advisory" items (e.g. Discord token) do not fail the exit code; they are surfaced but not
blocking. Preflight's own red count contributes to the non-zero exit.

## `provision.py` change

One added step near the end (after the racecast install, before/around the verification block):
copy the sibling `prepare-event.py` into `~racecast/prepare-event.py`, `chown racecast:`,
`chmod +x`. Idempotent (overwrite). When `provision.py` runs as a GCP startup-script (the file
is not beside it), this copy is best-effort and simply skipped with a note — the operator can
`scp` it up in that mode; the primary path is the documented `gcloud compute scp` of the
`tools/cloud/` directory.

## Documentation updates (same change)

- `tools/cloud/README.md`: a new "Prepare for an event (`prepare-event.py`)" section, and the
  copy-into-box note in §2.
- `src/docs/wiki/Cloud-Producer.md` §4: replace the hand-typed command list with
  `python3 prepare-event.py <league>`, keeping the individual commands underneath as "what it does",
  and note the freeze caveat for `racecast update`.

## Validation

- `python3 tools/lint.py` + `python3 tests/test_cloud_tools.py` (paper checks, like `provision.py`).
- **CPU dry-run possible for most of it:** the whole script is racecast-CLI orchestration with
  no GPU dependency, so it can be exercised on any box with racecast installed (the readiness
  checks degrade to red where the league isn't onboarded — which is the correct output).
- Idempotency: every step is safe to re-run (all underlying racecast prep commands are), so a
  second run after fixing a red readiness item just reports green.

## Risks / open items

- **`racecast update` on event day** contradicts the "freeze the toolchain before an event"
  principle. The operator chose to keep it (always run, with the preview guard). The freeze
  caveat is documented; the preview guard is the main safety net (it prevents the
  preview→stable downgrade that would actually break a live feed).
- **Cookie validity is not verifiable** — the script can confirm the cookies file is present
  and non-empty, but not that the YouTube session is still valid. Fresh export before each
  event remains the operator's responsibility (documented).
- **Non-interactive runs** can't answer the preview prompt; the safe default (keep preview) is
  correct for the current box but means an operator who *wants* the stable update from a
  `--command=` invocation must run it interactively (or `--no-update` + manual `racecast
  update`). Acceptable; documented.
