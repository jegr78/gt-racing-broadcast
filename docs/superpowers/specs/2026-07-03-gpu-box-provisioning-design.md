# GPU box provisioning — design

**Date:** 2026-07-03
**Status:** design (approved for spec review)
**Related:** #395 (cloud-producer spike), #421 (NVENC validation), runbook
`docs/superpowers/specs/2026-07-02-cloud-producer-spike-runbook.md` (Appendix A/B)

## Context & goal

The cloud-producer spike needs a GPU VM (GCP T4, Ubuntu 24.04) that runs OBS with
NVENC and the racecast toolchain, reachable over the tailnet. Appendix A of the runbook
already lists the copy-paste commands, but they are manual and interactive. This spec
defines a **reproducible provisioning script** so a fresh instance reaches
"ready to onboard a league" without hand-assembling each step, and so the setup is
documented for anyone (any league) who wants to stand up their own box.

## Lifecycle model (decided)

- **One long-lived instance**, provisioned once, reused for **all** events. Event/league
  switching is done with racecast **profiles** (`racecast profile use <name>`) — the
  same multi-profile-on-one-install model the toolkit already uses. Cost is controlled
  by **stopping** the VM between events (idle ≈ boot disk only), not by deleting it.
- **No machine images / snapshots.** Explicitly out of scope per the operator's
  preference. The provisioning script is the reproducibility mechanism instead.
- **Tailscale joins once.** State persists in `/var/lib/tailscale` across stop/start, so
  the box auto-reconnects on start and keeps a **stable `100.x` tailnet IP** even though
  the ephemeral public IP changes. No reserved static IP is needed.

## Non-goals

- The script does **not** handle league profiles, secrets, or cookies. Those are the
  per-league onboarding layer (below), done by the operator, not baked into the machine.
- No image/snapshot creation, no fleet/multi-host orchestration (a single box → Ansible's
  idempotent-fleet strength is not needed; a plain idempotent script is lighter).
- Not a shipped artifact. It is maintainer cloud glue under `tools/` (outside the
  `dist/` package, so the "no `.sh` shipped" build check — `os.walk(PKG)` in
  `tools/build.py` — does not apply).

## Architecture — two layers

**Layer 1 — machine layer (`tools/cloud/provision.py`, runs once on the box).**
Everything league-agnostic and secret-free. The script installs only what racecast does
**not** cover — NVIDIA driver, xfce desktop, Firefox (deb), RustDesk, passwordless sudo,
Tailscale **join** — and then delegates the toolchain and applications to the racecast
binary itself: `racecast install-tools` (yt-dlp/streamlink/ffmpeg/deno) and
`racecast install-apps` (OBS + Browser Source plugin, Tailscale, Companion, Discord).
It does **not** re-implement OBS/Tailscale installation — that is racecast's job, and
duplicating it would drift from the shipped installers.

**Layer 2 — per-league onboarding (operator, ongoing, NOT in the script).**
Copy the racecast profile up + fresh cookies + `racecast setup` (localizes the OBS scene
collection for the active profile) + import into OBS. Done once per league; switching
between already-onboarded leagues afterwards is just `racecast profile use <name>`. This
mirrors how racecast already separates machine state from profile state.

## `tools/cloud/provision.py` — specification

Runs as root — either invoked manually with `sudo ./provision.py`, or as a GCP
startup-script on first boot (both modes documented; see below). Every step is
**idempotent** (existence- or stamp-gated) so a re-run after a failed step is safe and
does not redo completed work. English-only, stdlib Python 3. It was first written in bash
with `set -euo pipefail`; the Python port keeps that fail-fast behaviour explicitly.

Steps, in order — split into "what racecast does not cover" and the two racecast
delegations:

1. **APT base.** `apt-get update && apt-get -y upgrade`, then base packages
   (`curl`, `python3`, `ca-certificates`). The explicit `apt-get update` also sidesteps
   the "Unable to locate package" failure on a fresh cloud image (the `install-tools`
   no-preceding-update bug noted in the spike memory).
2. **NVIDIA driver.** GoogleCloudPlatform `install_gpu_driver.py`; gate on
   `nvidia-smi` already succeeding so a re-run skips the (slow) driver build. Verify the
   Tesla T4 is listed. (`install_gpu_driver.py` may reboot → the `nvidia-smi` gate is
   what keeps the script idempotent across the reboot boundary.)
3. **Desktop + headless X.** xfce + lightdm autologin (OBS needs a real X11 session;
   Wayland screen-capture is limited → pin X11). On the GPU, write a headless
   `xorg.conf` via `nvidia-xconfig --allow-empty-initial-configuration
   --use-display-device=None --virtual=1920x1080` so X starts on the T4 with no monitor
   attached (the documented Ubuntu 24.04 + NVIDIA recipe). This gives OBS's OpenGL
   compositor + browser source GPU acceleration; NVENC itself is via `libnvidia-encode`
   and independent of the X display. Guarded by `has_nvidia_gpu` so a CPU dry-run skips it.
4. **Firefox (deb, not snap).** Install from the Mozilla APT repo so the profile lives at
   a standard path — snap confinement breaks cookie export. This is what makes
   `racecast cookies firefox` / `yt-dlp --cookies-from-browser firefox` work on the box.
   Skip if a working non-snap Firefox is already present.
5. **RustDesk.** Install the package. The permanent password + "Enable direct IP access"
   are set once by the operator in the GUI (can't be scripted safely) — the script only
   ensures RustDesk is present.
6. **Passwordless sudo.** Write `/etc/sudoers.d/90-racecast`
   (`<login-user> ALL=(ALL) NOPASSWD:ALL`), `chmod 440`, validate with `visudo -cf`
   before installing. Belt-and-suspenders over the GCP guest agent's own sudoers (which
   showed a password prompt in the earlier browser-SSH session).
7. **racecast binary.** Download the racecast binary for the box's architecture (the
   `update.asset_name()` logic — amd64 here) and put it on `PATH`.
8. **`racecast install-tools`.** yt-dlp / streamlink / ffmpeg / deno (apt-update-first +
   pinned yt-dlp/deno). Not re-implemented in the script — this is the shipped installer.
9. **`racecast install-apps --yes`.** OBS (via the obsproject PPA, Browser Source plugin
   included on x86-64), Tailscale, Companion, Discord — automated, unattended with
   `--yes`. The extra apps (Companion/Discord) are harmless on the box; Discord is
   plausibly wanted for the audio path. The script does **not** install OBS or Tailscale
   itself; it relies on this call.
10. **Tailscale join.** `install-apps` installed the Tailscale *package* but did not join
    the tailnet. The script runs `tailscale up --ssh`. Unattended join is **optional**
    via a `TS_AUTHKEY` env var (a reusable/ephemeral tagged pre-auth key, **never**
    committed to git, passed at runtime); when absent, the script prints the one-time
    browser-auth URL for the operator to complete. For the single persistent box the
    manual one-time join is the default; the authkey path exists for the "reproduce a new
    instance unattended" case.

**Verification block (end of script).** Report green/red per check instead of exiting
half-finished: `nvidia-smi` lists the T4, `ffmpeg -encoders | grep nvenc` shows the
NVENC encoders, `ldconfig -p | grep nvidia-encode`, `tailscale status` is up,
`firefox --version` is a deb build, `racecast --version` runs, and OBS is installed
(`which obs`). A red line tells the operator exactly which step to re-run.

## Execution modes (both documented)

- **Manual (default, for first-time setup with eyes on it):**
  ```
  gcloud compute instances create spike-gpu ...        # Appendix A step 1
  gcloud compute scp tools/cloud/provision.py spike-gpu:~/ --zone=...
  gcloud compute ssh spike-gpu --zone=...
    $ sudo ./provision.py                                # live output, re-runnable
  ```
- **GCP startup-script (reproduction one-liner, unattended):**
  ```
  gcloud compute instances create spike-gpu ... \
    --metadata-from-file startup-script=tools/cloud/provision.py
  # log: gcloud compute instances get-serial-port-output spike-gpu
  ```
  Same script; because it is root-safe and idempotent it runs correctly in both. Serial
  log is the only output channel here, so this mode is for the "any league reproduces a
  box" case rather than first-time debugging.

## SSH & sudo (the two explicit asks)

- **SSH.** Bootstrap via `gcloud compute ssh` (zero key management — OS Login / metadata
  keys). Steady-state via `tailscale ssh` (no keys, no open public port, ACL-gated,
  stable `100.x` address across stop/start) — same trust boundary as the rest of the
  product.
- **Passwordless sudo.** Guaranteed by the `/etc/sudoers.d/90-racecast` drop-in (step 7),
  independent of the guest agent.

## Deliverables

1. `tools/cloud/provision.py` — the script above.
2. `tools/cloud/README.md` — create instance → run provision.py (both modes) →
   per-league onboarding → stop/start cost control. Notes secrets (cookies, `TS_AUTHKEY`)
   never live in git.
3. **Runbook Appendix A rewrite** — from the current manual step-by-step copy-paste flow
   to: one persistent instance + `provision.py` + profile-per-league. Snapshots marked
   explicitly out of scope. Appendix B (NVENC #421) is unchanged.

## Validation (before spending GPU hours)

Paper checks: `python3 tools/lint.py` + `python3 tests/test_cloud_tools.py`; each command reviewed
against its upstream doc; idempotency guards read through. Then a three-tier confidence
build so the only thing left to prove on the (expensive) GPU box is the one GPU-specific
unknown — "does X start on the T4 with no monitor":

1. **CPU dry-run (pennies).** `provision.py` runs on a cheap non-GPU VM; a `has_nvidia_gpu`
   guard auto-skips the driver + xorg steps, so lightdm/autologin, RustDesk direct-IP over
   Tailscale, Firefox deb, `install-tools`/`install-apps`, and the verification block are
   all validated GPU-free. Run with `RACECAST_TAG=preview-main` so the *fixed* install
   code is what gets exercised.
2. **Isolated GPU smoke test (~15 min).** First thing on the GPU box, before OBS/onboarding:
   `provision.py` → reboot → check only the display lines of the verification block
   (`nvidia-smi` lists Xorg, `pgrep Xorg`, `DISPLAY=:0 glxinfo` renderer is the T4 not
   `llvmpipe`, RustDesk shows the desktop). Green = the risk is retired.
3. **Fallback.** If `--allow-empty-initial-configuration` misbehaves, feed a CustomEDID
   (fake a 1080p monitor). NVENC is via `libnvidia-encode`, independent of the X display,
   so encoding is not at risk while the desktop display is tuned.

The verification block is the on-box acceptance test; the NVENC proof is Appendix B (#421).
Full end-to-end can only be confirmed on the real GPU box — this spec makes that first run
correct, not unnecessary.

## Risks / open items

- **Headless X on the GPU.** The narrowest real risk. Mitigated by the known Ubuntu 24.04
  recipe (step 3) + the three-tier validation above; CustomEDID is the documented
  fallback. NVENC is decoupled from the display, so encoding is never blocked by it.
- **Preview-binary dependency.** The apt-update-first fixes to `install-tools`/
  `install-apps` (#408/#412) are on `main` but unreleased. Until a stable release, the box
  MUST install `RACECAST_TAG=preview-main` (`gh workflow run preview.yml --ref main`),
  else provisioning hits the "Unable to locate package" bug on a fresh image. `latest`
  (stable) is the correct default once those fixes ship.
- **NVIDIA driver + reboot.** `install_gpu_driver.py` may require a reboot; the script
  detects "driver already loaded" (`nvidia-smi` gate) to stay idempotent across the reboot
  boundary; the xorg step re-runs after the reboot on a re-invocation.
- **Snap-vs-deb Firefox.** If the Mozilla APT repo path changes, cookie export regresses
  silently — the verification block asserts a deb build to catch this.
- **RustDesk / OBS password + scene import** remain manual GUI one-time steps by design;
  the script cannot and should not automate them.

## Post-run amendment (2026-07-04)

The first full GPU-box run superseded parts of steps 2–3 above and the driver risk item.
`provision.py` and the runbook's "Post-GPU-run findings" section are the source of truth;
in short: (1) the driver is `nvidia-open` from the NVIDIA CUDA apt repo, **not**
GoogleCloudPlatform/`install_gpu_driver.py` (its pinned 550 fails to build on Ubuntu
24.04's kernel 6.17); (2) headless X is a **hand-written** `/etc/X11/xorg.conf` (BusID from
`lspci`, single 1920×1080), because `nvidia-xconfig` is not shipped with the open driver;
(3) the preview-binary dependency now also covers the Linux streamlink-venv (≥ 8.2.0) and
obs-pipewire-audio installs (#395), not only the apt-update fixes; (4) EU home region is
`europe-west4` (L4, `g2-standard-8`) — a stopped GPU VM re-competes for capacity on start.
