# Cloud-Producer Spike — Execution Runbook (Issue #395)

**Date:** 2026-07-02
**Status:** Runbook approved, ready to execute — this document IS the deliverable-methodology
for the #395 spike; the spike itself produces a **findings write-up** (go/no-go per stage).
**Scope:** A staged, abort-fast investigation to decide **GO / NO-GO** on moving the producer
station (relay + OBS + broadcast encode/upload) to an **on-demand cloud GPU instance** spun up
per event, so the home machine only runs the operator's own commentary stream + directing in
the browser.

> This runbook refines the plan in issue #395. It changes two things from the original issue
> and locks the provider choice — see **Decisions** below. Everything else (motivation, the
> load table, the staged abort structure) stands as written in #395.

## Decisions locked in brainstorming (2026-07-02)

1. **Headless OBS is removed entirely.** The original issue assumed a headless OBS bring-up
   (Xvfb/EGL). Rejected: too much uncertainty for almost no real value in a spike whose whole
   job is to *observe and validate*. We run OBS on a **GPU VM with a real graphical desktop**
   reached over remote access (NICE DCV / VNC / NoMachine), exactly like a local OBS. The
   desktop **replaces Xvfb** (OBS renders to the real X session), so it is *less* setup, not
   more — and every render/encode/audio check is done with eyes on the screen.
   - Headless is a *production* cost-optimization, not a spike requirement. It is **deferred
     to Post-GO follow-ups and may never be needed** — in production you start OBS and detach;
     an idle desktop session costs almost nothing on a T4.
2. **No Docker image for the spike.** Per the issue's own note, a baked image is a *Post-GO*
   investment. For the CPU IP-test (Stage 1) a container adds only friction; for the GPU/OBS
   stages Docker is actively painful (nvidia-container-toolkit + OBS + browser-source +
   PulseAudio in a container). Reproducibility across the Stage-1 providers comes from a small
   **throwaway Python bootstrap** (the "no `.sh`" rule applies only to *shipped* tooling — a
   scratch spike script is fine) or simply the copy-pasteable commands in this runbook.
   - **If GO, the image is a provider VM snapshot, not Docker** — precisely because of
     OBS/NVENC/desktop-audio.
3. **Provider selection — updated 2026-07-02 after the live run (see Session log):**
   - **The whole spike runs on GCP.** Stage 1 confirmed the **GCP datacenter IP passes the
     YouTube *and* Twitch feed pull** (resolve + sustained), so — per the coupling below — the
     GPU box stays on the same validated provider/IP. GCP also has documented OBS + T4 + NVENC
     support and lets us pick the OS (Ubuntu 24.04, so the amd64 binary just runs).
   - **Stage 1 (CPU, IP-reputation test):** done on a **GCP free-tier `e2-micro` (us-central1,
     Ubuntu 24.04)** for ~€0. *(Superseded from the original plan: Hetzner = new-account ID
     verification, declined; Paperspace Core = stock templates only Ubuntu 20.04 / CentOS 7,
     too old for the toolchain — deno needs glibc ≥2.35, the binary needs 2.38.)*
   - **Stage 2–4 (GPU):** a **GCP T4 (`n1` + T4) or L4 (`g2`)** in `us-central1` with a desktop,
     reached over remote access (Chrome Remote Desktop / VNC / NoMachine). **GCP grants new
     accounts 0 GPU quota → request a T4/L4 quota increase up front (step 0); approval can take
     hours to a couple of business days.** AWS `g4dn.xlarge` remains the documented fallback
     (same quota-lead-time caveat).

## The coupling that the original issue under-specified

**Stage 1 must test the IP of the exact provider Stage 2+ will run on.** Otherwise we validate
the datacenter-IP feed pull on Hetzner, then build the OBS station on Paperspace — and
Paperspace's IP is the one that gets bot-blocked. The GPU provider's IP is therefore a
first-class Stage-1 test target, alongside Hetzner and DigitalOcean.

---

## Stage 0 — Prerequisites (local, ~30 min, €0)

**Cookies (headless has no interactive browser login → export locally, upload):**

1. Locally, sign in to YouTube in **Firefox** using a **dedicated throwaway Google account**
   (not your main — the cookies are a live session uploaded to a rented box; a throwaway
   account limits blast radius).
2. `racecast cookies firefox` → writes `runtime/yt-cookies.txt` (Netscape format, never
   hand-edit). Twitch only if the league uses gated feeds: `racecast cookies twitch firefox`.
3. Copy to each test box over **scp/SFTP** (encrypted):
   `scp runtime/yt-cookies.txt user@server:~/racecast/runtime/yt-cookies.txt`.
4. Use **fresh, valid** cookies so a Stage-1 failure is unambiguously **IP-based**, not
   cookie-based. Cookies expire/rotate → in production expect to **re-export fresh before each
   event**. Never commit (already gitignored).

**Throwaway league profile:** create a profile with a *real* schedule (real unlisted/live
commentator URLs) so the feed pull hits a real bot-check. `racecast profile new spike-395`.

**Compounding-risk note (record it, don't fight it yet):** cookies exported on a home IP but
used from a datacenter IP (session origin ≠ usage IP) can trip the bot-check *harder* than
either alone. Stage 1 measures the real combined behavior — that is the point.

## Stage 1 — Datacenter-IP feed pull ⚠️ THE K.-O. RISK, TEST FIRST (~30–45 min/provider, < €1 total)

**Question:** Does the relay's feed pull work from a datacenter IP, or does YouTube
throttle/block it?

Run **on each of the three Stage-1 boxes** (Hetzner, DigitalOcean, the GPU provider):

1. Install the toolchain: `racecast install-tools` (yt-dlp/streamlink/ffmpeg/deno). Ubuntu LTS.
2. **Raw probe:** `yt-dlp -g --cookies runtime/yt-cookies.txt <real unlisted/live URL>` → must
   resolve an HLS URL. Feed it to `streamlink` and confirm **bytes flow**.
3. **Relay-level:** `racecast --profile spike-395 relay run` (foreground/debug). Watch
   `feed_A/B` logs for a **stable pull with no 403/429 churn for ≥ 15 min**.
4. Repeat for a **Twitch** source (streamlink Twitch-plugin path) **if the league uses Twitch**.

**ABORT if:** feeds 403/429 or drop repeatedly from *any* datacenter IP — most critically the
GPU provider's. Then the whole cloud-feed path is dead regardless of server power → record
findings, stop, and evaluate alternatives (residential proxy, or "relay stays home, only OBS
moves to cloud") **before** spending on GPU.

**Record per provider:** pass/fail, 403/429 count over 15 min, any throttle/reconnect pattern,
resolved-URL latency. → *Stage-1 findings table* (template at the end).

## Stage 2 — OBS on a GPU desktop VM (GCP T4/L4) (~half to full day first time, ~$3–5)

> Rewritten from the issue's "headless OBS PoC". No Xvfb, no EGL. OBS runs on a **real desktop**
> you connect to and operate normally.

1. Spin up a **GCP GPU VM** (`n1-standard-4` + a **T4**, or a `g2` **L4**) in `us-central1`,
   **Ubuntu 24.04**, and add a desktop reachable over remote access (Chrome Remote Desktop /
   VNC / NoMachine). Requires the GPU quota from step 0. Install the NVIDIA driver.
2. `racecast install-tools` + `racecast install-apps` (OBS). **On x86-64 Linux the OBS PPA
   ships the Browser Source plugin — `racecast obs-browser` is only needed on aarch64, which we
   avoid by choosing an x86-64 GPU VM.** On a no-GPU/VM host also disable Browser-Source
   Hardware Acceleration (already documented) — verify whether it's needed on the real GPU.
3. Localize + import the scene collection: `racecast setup` → import into OBS (normal GUI).
4. **Verify with eyes on the screen:**
   - The relay-served **HUD browser source** renders in OBS.
   - **NVENC encodes 1080p60** (T4 datacenter GPU → unlimited NVENC sessions; encode + remote
     desktop coexist fine).
   - Push a short test encode to a **private/unlisted RTMP target** and confirm it arrives.
5. **Virtual audio:** confirm a **PulseAudio/PipeWire null-sink** works for any OBS audio input
   the broadcast needs. **Open question to settle here (do not assume):** is **Discord audio**
   actually in our broadcast path, or does commentary audio arrive **via the pulled feed
   itself**? If commentary rides the pulled feed (normal for commentator feeds), the virtual
   audio need shrinks dramatically. Settle this against the crew's real setup.

**ABORT if:** NVENC or the browser source can't be made reliable for a multi-hour run, or the
desktop GPU VM is unstable.

**Record:** boot-to-desktop time, OBS stability over a multi-hour idle+encode soak, NVENC
1080p60 CPU/GPU headroom, whether virtual audio was needed at all, Discord-audio answer.

## Stage 3 — Remote operation over the tailnet (~1–2 h)

1. Join the box to Tailscale (`racecast tailscale up`); reach the **Control Center** at
   `http://<vps-tailscale-ip>:8089` from the operator's laptop (**Tailscale = the auth**; the
   CC stays localhost-bound, no new auth built — `RACECAST_UI_PASSWORD` stays reserved).
2. **Direct from the laptop via `/console/panel`** (Funnel or tailnet): scene switches, source
   toggles, audio, transitions, cues, feed handover — all **relay-mediated** — and confirm they
   drive the **cloud** OBS. (The Director Panel needs no OBS IP/port/password — it works fully
   over Funnel with just the per-person token. This is already the case; Stage 3 confirms it
   end-to-end against a remote OBS.)
3. **Trust boundary:** confirm the relay control port **8088 is NOT publicly exposed** (tailnet
   only); only `/console` may be public via Funnel, as today. OBS-WebSocket is never funnelled.

**ABORT if:** the panel can't reliably drive the cloud OBS, or the trust boundary can't be kept.

**Record:** panel→cloud-OBS action reliability, any control latency, confirmation that 8088 is
tailnet-only.

## Stage 4 — Cost / latency / decision (~1–2 h, ~$1–2)

1. **Non-live dry run, end-to-end:** operator commentary (home) → YouTube → cloud relay pull →
   cloud OBS → broadcast out (unlisted target). Measure **added end-to-end latency** and confirm
   it's acceptable for the relay's ping-pong design.
2. Record **actual instance cost for a ~6 h event** and **boot-to-ready time** (Stage 2's
   boot-to-desktop + toolchain + scene import + cookie upload).
3. **Decide GO / NO-GO** using the findings tables.

---

## Duration & cost summary (verify current pricing at run time)

| Stage | What | Instance | Wall-time | Cost |
|---|---|---|---|---|
| 0 | Cookies, throwaway account, profile | local | ~30 min | €0 |
| 1 | Datacenter-IP feed pull, **×3 providers** | CPU VPS (Hetzner ~€0.007/h, DO ~$0.009/h) | ~30–45 min each → ~2–3 h | **< €1** |
| 2 | OBS on GPU desktop VM (**the real time sink**) | T4 GPU ~$0.20–0.55/h | ½–1 day first time | ~$3–5 |
| 3 | Panel drives cloud OBS over tailnet | same GPU VM | ~1–2 h | in Stage-2 cost |
| 4 | End-to-end dry run, latency, decision | same GPU VM | ~1–2 h | ~$1–2 |
| **Total spike** | | | | **< ~$15** |

### Production cost model — running vs. fixed (verified GCP pricing, us-central1 on-demand, 2026-07)

**Recommended instance: T4** (`n1-standard-4` + 1× T4), not L4 — a single 1080p60 NVENC encode
is well within a T4, datacenter T4s have **no NVENC session cap**, and it is ~25 % cheaper than
an L4. L4 (`g2-standard-4`) is the documented alternative if a T4 proves short.

**Baseline event weekend:** an **8 h race event** + a **1.5 h qualifying session the day before**
= **9.5 h billable**, plus ~1 h boot / scene-import / test overhead ≈ **~10.5 h** wall-time.

*Running cost (accrues ONLY while the VM is running):*

| Component | T4 combo | L4 combo | Notes |
|---|---|---|---|
| Compute (VM + GPU) | ~$0.54/h | $0.71/h | n1-standard-4 $0.19 + T4 ~$0.35; g2-standard-4 bundles 1× L4 |
| Egress (OBS output → YouTube/Twitch) | ~$0.43/h | ~$0.43/h | 1080p60 ≈ 8 Mbps ≈ 3.6 GB/h × $0.12/GB — **measure in Stage 4**; feed *pulls* in are free |
| Ephemeral external IPv4 | $0.004/h | $0.004/h | only while running |
| **All-in active** | **~$0.98/h** | **~$1.15/h** | |

- **Qualifying (1.5 h):** ~$1.5 · **Event (8 h):** ~$7.8 · **per weekend (9.5 h + ~1 h setup):
  ~$10 (T4)** / ~$11 (L4).
- Egress is **not** negligible (~$3–4 of a weekend) — the earlier "bandwidth is a non-issue"
  note was optimistic; only the *outbound* broadcast is billed, feed ingress is free.

*Fixed cost (accrues even while the VM is STOPPED between events):*

| Component | Price | 50 GB / month |
|---|---|---|
| Boot disk (pd-standard) — persists as long as the disk exists | $0.04/GB/mo | **~$2/mo** (pd-balanced would be $5) |
| Reserved static IP — **only if** you pin one IP | $0.01/h idle | +$7.30/mo — **skip it**: Stage 1 showed GCP IPs pass generally, an ephemeral IP is fine |
| Snapshot / baked VM image (post-GO) | $0.026/GB/mo | ~$0.5–0.8/mo |

- **Idle baseline: ~$2/month** (just the stopped boot disk, no static IP).
- **2 event weekends/month, all-in:** ~$20 active + ~$2 fixed ≈ **~$22/month**, billed only while
  running. **On-demand, never spot** for a live run (spot can be reclaimed mid-event).
- **Quota is free** — it is an allocation *ceiling*, not a reservation or charge; you pay only for
  resources actually created and running. (Distinct from Reservations / Committed-Use, which *do*
  cost — not needed here.)

**The real cost driver is not the instance (< $15 for the whole spike) — it's engineering hours
on Stage 2.** And the whole thing can die at Stage 1 in ~1 h for < €1. That ordering is the
entire value of the staged structure: buy the cheapest information first.

## Risks & open questions

- **Datacenter IP vs. YouTube bot-check (Stage 1) — the dominant risk.** Compounded by the
  cookie session-origin ≠ usage-IP factor above.
- **Headless cookie refresh is impractical** → export locally, upload; re-upload fresh before
  each event.
- **Discord audio in the broadcast path?** — unresolved; drives the virtual-audio need. Settle
  in Stage 2.
- **Platform ToS** for relaying third-party streams from a datacenter — note and respect.

## Deliverable & Post-GO follow-ups

**Deliverable of the spike:** a findings write-up (go/no-go with evidence per stage). **No
`src/` changes are expected from the spike itself beyond documentation.**

**If GO**, follow-up issues:
1. **Bake the cloud image** — a **provider VM snapshot** (OBS + desktop + virtual audio + scene
   collection pre-imported) for "boot → event-ready in minutes". **Not Docker.**
2. **GPU-desktop + virtual-audio setup docs.**
3. **Operator runbook:** "spin up → run event → tear down."
4. *(De-prioritized / maybe never)* Headless hardening as a cost optimization — only if the
   idle-desktop cost ever proves to matter.

---

## Post-GPU-run findings (2026-07-04, europe-west4 · L4)

The first full GPU-box run (create → provision → onboard a league → live feed rendered
in cloud OBS → teardown). GO on every hard unknown; it also surfaced six real gaps that
are now fixed in the repo (do NOT re-derive them on the next box):

**Region — EU is the production home (`europe-west4`), not `us-central1`.** For an EU
operator the RTT to `europe-west4` is ~20 ms vs ~110 ms to `us-central1`; that latency is
what RustDesk remote-control feels (it does **not** affect the feed pull or the audience).
GPU pricing is within a few % across the two regions, and the account already carried
`NVIDIA_T4_GPUS=1` + `NVIDIA_L4_GPUS=1` quota in `europe-west4` — so no new quota request
was needed there. Use `europe-west4`; keep `us-central1` only as a capacity fallback.

**Capacity — stop/start re-competes for the GPU.** Stopping a GPU VM releases the GPU, so
every `start` re-acquires zonal capacity and can fail with `ZONE_RESOURCE_POOL_EXHAUSTED`.
T4 was exhausted across **all** `us-central1` zones (a/b/c/f) **and** `europe-west4` during
the run; **L4 (`g2-standard-4`) in `europe-west4-c` succeeded** and is the recommended
default for the EU box. A create/start retry loop must capture gcloud's real exit code
(don't mask it behind a pipe). Guaranteed capacity = a Reservation, which bills continuously
— out of scope for the stop-between-events model.

**vCPU floor — re-evaluated.** `g2-standard-4` / `n1-standard-4` give **4 logical cores**. The
original preflight FAILed below 6 cores — a floor tuned for a software-x264 producer, wrong for
a GPU box where NVENC offloads the encode. Preflight is now **GPU-aware** (`detect_nvidia_gpu`):
with an NVIDIA GPU the CPU floor relaxes to FAIL `<2` / WARN `<4` / PASS `≥4`, so
`g2-standard-4` passes **green** (it validated a real broadcast here). RAM was likewise retuned
(PASS from 12 GB, so the box's 16 GB is green). Size up to `g2-standard-8` only for extra core
headroom on a busy multi-feed event.

**NVIDIA driver — `nvidia-open`, not the deprecated installer.** GCP's
`install_gpu_driver.py` pins driver 550.54.15, whose proprietary modules **fail to build**
against Ubuntu 24.04's current kernel (6.17). `provision.py` now installs `nvidia-open`
(open kernel modules, DKMS) from the NVIDIA CUDA apt repo. (F2)

**Headless X — hand-written `xorg.conf`.** `nvidia-xconfig` is not shipped with the open
driver. `provision.py` writes `/etc/X11/xorg.conf` directly (BusID from `lspci`, a single
1920×1080 mode — a larger `Virtual` desyncs RustDesk's absolute mouse mapping). (F3)

**streamlink too old on Linux — the "relay bug" that wasn't.** The box's feed aborted with
`unrecognized arguments: --http-cookies-file`. Root cause: **not** the relay (its code is
correct) but Ubuntu 24.04's apt streamlink **6.6.2**, which predates `--http-cookies-file`
(added in streamlink **8.2.0**). macOS/Windows producers get 8.x from brew/winget, so only
Linux was affected. `install-tools` now installs a pinned streamlink venv (≥ 8.2.0) on
Linux and `preflight` FAILs below the floor. (F1)

**OBS PipeWire audio plugin absent.** OBS's apt/PPA package ships no
`obs-pipewire-audio-capture`, so Discord interview audio couldn't capture until it was
hand-installed. `install-apps` now installs the pinned upstream plugin on Linux. (F4)

Because F1/F4 live in `install-tools`/`install-apps` and are unreleased, a fresh box must
install `RACECAST_TAG=preview-main` until they ship in a stable release (see Appendix A
step 2).

---

## Appendix A — GCP T4/L4 provisioning (persistent instance + provision.py)

Model: **one long-lived instance**, provisioned once with `tools/cloud/provision.py`,
reused for all events by switching racecast **profiles**. Stop it between events for cost;
the tailnet IP is stable across stop/start. **No machine images/snapshots** — the script
is the reproducibility mechanism (full detail + the per-league onboarding and cost-control
commands live in `tools/cloud/README.md`). Assumes `gcloud` is authed
(`gcloud auth login`; `gcloud config set project <PROJECT_ID>`).

**Step 0 — GPU quota (do FIRST; the only real lead time; quota is free).** New accounts
have GPU quota 0. Use **`europe-west4`** for an EU operator (see Post-GPU-run findings —
~20 ms vs ~110 ms RTT for RustDesk). Request in the console (**IAM & Admin → Quotas &
System Limits**), filter your region, request **≥ 1** for `NVIDIA_L4_GPUS` (the recommended
GPU — see step 1) and/or `NVIDIA_T4_GPUS`, plus `GPUS_ALL_REGIONS` (global). Inspect from
the CLI:
```bash
gcloud compute regions describe europe-west4 \
  --format="table(quotas.metric,quotas.limit,quotas.usage)" | grep -i gpu
```
Approval: hours to ~2 business days. The VM cannot be created until it lands. Quota is an
allocation *ceiling*, not a reservation or a charge — you pay only for resources actually
running (distinct from Reservations / Committed-Use, which do cost and are not needed here).

**Step 1 — create the VM (only after quota granted).** GPU VMs cannot live-migrate →
`--maintenance-policy=TERMINATE` is required. **L4 (`g2-standard-4`) in `europe-west4-c`**
is the validated EU default (T4 was capacity-exhausted across all tried zones — see
findings). The L4 is bundled into the `g2` machine type, so there is **no `--accelerator`
flag**. `g2-standard-4` (4 vCPU) passes preflight green — the GPU relaxes the CPU floor (see
findings); size up to `g2-standard-8` only for extra core headroom on a busy multi-feed event.
```bash
gcloud compute instances create spike-gpu \
  --zone=europe-west4-c \
  --machine-type=g2-standard-8 \
  --maintenance-policy=TERMINATE \
  --provisioning-model=STANDARD \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-type=pd-standard \
  --boot-disk-size=50GB
```
T4 fallback (needs the `--accelerator` flag): `--machine-type=n1-standard-8
--accelerator=type=nvidia-tesla-t4,count=1`, e.g. in `us-central1-a` (T4 zones: a, b, c, f).
`pd-standard` (not the "Balanced" default) → ~$2/mo idle disk. Ephemeral IP (Stage 1 showed
GCP IPs pass generally — no reserved static IP). `STANDARD`, never spot, for a live run.
A create/start can fail with `ZONE_RESOURCE_POOL_EXHAUSTED` — retry across zones (and
capture gcloud's real exit code, don't mask it behind a pipe).

**Step 2 — provision the machine layer (once).** Copy up and run the script; it installs
the NVIDIA driver, xfce, Firefox (deb), RustDesk, passwordless sudo, joins Tailscale, and
runs `racecast install-tools` + `racecast install-apps --yes`. Idempotent — re-run after
any red line.
```bash
gcloud compute scp tools/cloud/provision.py spike-gpu:~/ --zone=europe-west4-c
gcloud compute ssh spike-gpu --zone=europe-west4-c
  $ sudo ./provision.py
```
Until the Linux `install-tools`/`install-apps` fixes ship in a stable release —
apt-update-first (#408/#412) **and** the streamlink-venv + obs-pipewire installs (#395) —
install the current main preview instead: `gh workflow run preview.yml --ref main` once,
then `sudo RACECAST_TAG=preview-main ./provision.py`. Reproduction alternative
(unattended, any league): pass the script as a startup-script at create time —
`--metadata-from-file startup-script=tools/cloud/provision.py` — and read the serial-port
output. Both modes are documented in `tools/cloud/README.md`.

**Step 3 — finish in the GUI (once, over RustDesk on the `100.x` Tailscale IP).** Set the
RustDesk permanent password + "Enable direct IP access". If `nvidia-smi` was not ready
during provisioning, reboot once so the autologin X session starts (RustDesk needs it).

**Step 4 — onboard a league (once per league, then reuse via `racecast profile use`).**
Copy the profile + fresh cookies up, `racecast setup` to localize the OBS scene collection,
import it into OBS. See `tools/cloud/README.md` §4.

**Step 5 — validate with eyes on the screen (Stage 2 / NVENC #421).** HUD browser source
renders; **NVENC encodes 1080p60** (Appendix B checklist); short test encode →
private/unlisted RTMP arrives. Then continue Stage 3 (panel drives cloud OBS) and Stage 4.

**Cost control — stop between events (idle ≈ $2/mo boot disk).**
```bash
gcloud compute instances stop  spike-gpu --zone=europe-west4-c
gcloud compute instances start spike-gpu --zone=europe-west4-c   # ephemeral public IP may change; tailnet IP stays
```
Note: `start` re-competes for zonal GPU capacity (stopping released it) and can hit
`ZONE_RESOURCE_POOL_EXHAUSTED` — retry, or across zones. A Reservation guarantees capacity
but bills continuously (out of scope for stop-between-events).

---

## Appendix B — NVENC 1080p60 verification (#421)

Goal: **prove** OBS encodes 1080p60 via the T4's NVENC and does not silently fall back to x264.
Runs on the GPU box after the driver + OBS are up (Appendix A steps 2 + 4).

**B.1 — encoder present (prereqs).**
```bash
nvidia-smi                                   # Tesla T4 listed, driver loaded
ldconfig -p | grep -i nvidia-encode          # libnvidia-encode.so present
ffmpeg -hide_banner -encoders | grep nvenc   # must list h264_nvenc / hevc_nvenc
```
All three must pass; a missing `libnvidia-encode` means the driver install is incomplete (redo A.2).

**B.2 — OBS sees NVENC.** Settings → Output → Output Mode **Advanced** → the **Encoder** dropdown
must offer **"NVIDIA NVENC H.264"** (x264-only = OBS can't see it). Help → Log Files → current log
should show NVENC init, not "NVENC not available / falling back".

**B.3 — configure 1080p60.** Video: base + output 1920×1080, FPS **60**. Output → Streaming:
Encoder NVENC H.264, Rate Control **CBR**, Bitrate **~8000 kbps**, Keyframe **2 s**, Preset
**P5/Quality**, Profile **high**.

**B.4 — real encode + proof the GPU is working.** With the HUD browser source + a feed active, run
a **~3 min** test recording (or stream to an unlisted RTMP target). In parallel on the box:
```bash
nvidia-smi dmon -s u      # watch the "enc" column
```
- **`enc` (encoder utilisation) > 0 %** = NVENC is genuinely doing the work; `nvidia-smi` also
  lists OBS as a GPU process.
- `htop` → **OBS CPU stays low** (software x264 1080p60 would peg several cores). **enc > 0 AND
  low CPU together** is the proof against a silent x264 fallback.

**B.5 — output integrity.** OBS Stats dock (View → Docks → Stats): **0 "skipped frames due to
encoding lag"** across the run; the recording plays back as clean 1080p60 / the unlisted RTMP
target shows the stream arriving at 1080p60.

**Pass criteria:**

| Check | OK value |
|---|---|
| NVENC H.264 in the OBS dropdown | present |
| `nvidia-smi` `enc` during encode | **> 0 %** |
| OBS CPU during encode | low (no core maxed) |
| encoding-lag skips (OBS Stats, ~3 min) | **0** |
| output 1080p60 | clean, arriving |

**Failure → cause:**
- x264-only / log "NVENC not available" → driver not loaded (`nvidia-smi` fails) or
  `libnvidia-encode` missing → reinstall driver; confirm the OBS build has FFmpeg NVENC.
- `enc` stays 0 while OBS "encodes" → silent x264 fallback → re-select NVENC, check the log.
- encoding-lag skips climbing → on a T4 at 1080p60 this is a **config** issue, not a GPU limit →
  lower preset/bitrate.
- Datacenter T4 has **no NVENC session cap** (consumer GeForce: 3–8), so remote-desktop encode +
  OBS NVENC coexist — just confirm both run, nothing to "fix".

Record into the **Stage 2 findings table** ("NVENC 1080p60 headroom"): `enc` %, CPU %, skipped
frames.

---

## Session log — 2026-07-02 (Stage 0 + Stage 1 core)

**Result: Stage 0 + the Stage-1 K.-o. gate PASS on GCP, for ~€0.** The dominant risk
(datacenter IP vs. YouTube bot-check) **did not fire** — see the table below.

- **amd64 software (never confirmed since pre-v1.0.0):** racecast v1.4.0 amd64 binary runs
  on Ubuntu 24.04 (glibc 2.38 ok); deno 2.8.3 installs + runs on amd64/24.04.
- **GCP datacenter IP `35.202.91.202` (us-central1) resolves YouTube via `yt-dlp -g` +
  cookies + deno for BOTH a VOD and a real LIVE stream** (`source/yt_live_broadcast`,
  `live/1`) → HLS `index.m3u8`. No 403 / no "not a bot" / no "only images". Cookies were
  exported on a home IP and used from the datacenter IP (the session-origin-mismatch factor)
  and it still worked. **This contradicts the "all cloud IPs are blocked" web-research
  assumption** — GCP + cookies + deno works. The earlier "only images" failure was purely
  **missing deno on PATH**, not the IP.
- **Provider pivots:** Hetzner = new-account ID verification (declined). Paperspace Core =
  stock templates only Ubuntu 20.04 / CentOS 7 → too old (Python 3.8, glibc 2.31); the amd64
  binary needs glibc 2.38, deno needs ≥2.35, current yt-dlp needs Python ≥3.10. Not viable.
  → GCP is the working provider.
- **Product bug found:** `racecast install-tools` runs `apt-get install` with **no preceding
  `apt-get update`** → "Unable to locate package" on a fresh cloud image. Filed as an issue.
- **GCP free-tier gotchas:** the create-instance estimator shows gross list price (never the
  free-tier credit); the default "Balanced" boot disk is not free → pick **Standard pd ≤30 GB**.

**Stage-1B sustained pull — DONE (PASS):** a 15-min streamlink pull (the relay's real client)
of a real 1080p live stream (itag 96) from the GCP IP ran the full 900 s with **0
403/429/reconnect/errors** and ~1.4 Mbps steady ingest. The YouTube path of Stage 1 is now
**fully green for GCP** (resolve VOD+live + sustained 1080p pull). Note: the relay's main-feed
format is `-f "b[height<=1080]/b"` (POV capped 720p) — the test used yt-dlp's default which
also landed on 1080p, so it is production-representative.

**Twitch — DONE (PASS):** a 5-min streamlink Twitch-plugin pull of a live 1080p60 channel from
the GCP IP ran the full 300 s with **0 errors**, ~9.5 Mbps steady; ads were auto-skipped
(`Will skip ad segments`) as expected — no blocking. **Stage 1 is now fully green on GCP for
BOTH YouTube and Twitch** (resolve + sustained pull), for ~€0. The dominant K.-o. risk
(datacenter IP vs. platform bot-check) is **retired for GCP.**

**Still open (not the K.-o. risk):** an **unlisted** commentator URL (identical mechanism;
unlisted is a cookie-access, not IP, question), and — belt-and-suspenders — a full
`racecast relay run` with a real profile. Both can ride the real Stage-2 box.

## Findings tables (fill during the run)

### Stage 1 — Datacenter-IP feed pull
| Provider / region | ASN | Raw `yt-dlp -g` | streamlink bytes | Relay ≥15 min | 403/429 count | Twitch (if used) | Verdict |
|---|---|---|---|---|---|---|---|
| **GCP e2-micro / us-central1** | 15169 | **PASS** (VOD + LIVE) | **PASS** (1080p, ~1.4 Mbps) | **PASS** (15 min raw streamlink) | **0** | **PASS** (5 min 1080p60) | **PASS (YouTube + Twitch)** |
| Hetzner CX22 / EU | 24940 | — (ID-verify blocked signup) | | | | | n/a |
| Paperspace Core | — | — (OS templates too old: 20.04/CentOS7) | | | | | n/a |

### Stage 2 — OBS on GPU desktop VM
| Check | Result | Notes |
|---|---|---|
| Boot → desktop time | | |
| HUD browser source renders | | |
| NVENC 1080p60 headroom | | |
| Multi-hour soak stable | | |
| Virtual audio needed? | | |
| **Discord audio in broadcast path?** | | settle here |

### Stage 3 — Remote operation
| Check | Result | Notes |
|---|---|---|
| CC reachable over tailnet | | |
| `/console/panel` drives cloud OBS | | |
| Control latency | | |
| 8088 tailnet-only confirmed | | |

### Stage 4 — Cost / latency / decision
| Metric | Value |
|---|---|
| Added end-to-end latency | |
| Actual 6 h event cost | |
| Boot-to-ready time | |
| **GO / NO-GO** | |
