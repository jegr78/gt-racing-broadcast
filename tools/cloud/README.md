# GPU box provisioning (cloud-producer spike, #395)

`provision.sh` brings a fresh GCP GPU VM (Ubuntu 24.04, amd64) to "ready to onboard a
league": NVIDIA driver, xfce desktop (autologin), Firefox (deb), RustDesk, passwordless
sudo, Tailscale join, plus the racecast toolchain and applications (`install-tools` /
`install-apps`). It installs only what racecast does not cover and delegates the rest to
the racecast binary: it never re-implements the OBS/Tailscale installers.

**Log in as `racecast`.** You connect to the box **directly as the `racecast` user**
(`gcloud compute ssh racecast@racecast-box`); the GCP guest agent auto-creates that user on
first connect (OS Login is off → metadata SSH keys). `provision.sh` then installs the whole
event stack **directly into `/home/racecast`** (binary at `/home/racecast/racecast`, with
`profiles/` + `runtime/` alongside. The home IS the install root, no nesting). Because you
ARE `racecast`, every event command is plain `racecast <cmd>`: no `sudo`, no second user.

**Model:** one long-lived instance, reused for all events by switching racecast
**profiles** (`racecast profile use <name>`). Stop the VM between events for cost; the
tailnet IP is stable across stop/start. No machine images/snapshots. This script is the
reproducibility mechanism instead (any league can stand up its own box the same way).

## Providers — AWS (current) and GCP

There are two boxes; both are controlled the same way once up (SSH in as `racecast`,
`racecast <cmd>`), and both are managed day-to-day by the control wrappers in §5
(`aws-box.sh` / `gcp-box.sh` → `status` / `start` / `stop` / `ssh`).

- **AWS** (current production box): `g4dn.xlarge` (Tesla T4) in `eu-central-1a`,
  instance `i-04d70428c19a484ef`. You reach it **directly over SSH with a key pair**,
  `ssh -i ~/.ssh/racecast-box.pem racecast@racecast-box-aws` (the tailnet MagicDNS
  host): not via `gcloud`. Everything below the create/login layer (provisioning
  the machine, onboarding a league, per-event prep, going live) is identical.
- **GCP**: `g2-standard-8` (L4) in `europe-west4-b`, instance `racecast-box`. Login is
  `gcloud compute ssh racecast@racecast-box` (OS Login off → the guest agent creates
  the `racecast` user from the metadata SSH key). The create + `provision.sh`
  walkthrough in §1–§2 is written for this path.

The create/login layer differs per provider (§1 covers GCP); the racecast layer
(§3–§4b: onboarding, cookies, Discord, `prepare-event.sh`, `event start`) is
provider-agnostic: it is all plain `racecast <cmd>` as the `racecast` user.

## 1. Create the instance (once)

Requires GPU quota (free; request first, see the runbook Appendix A, Step 0) and a
`gcloud` authed to your project. **L4 in `europe-west4-b`** is the EU default (where the
current box runs; T4 was capacity-exhausted across the zones tried, and L4 capacity moves
between `-b`/`-c`: retry the sibling zone on `ZONE_RESOURCE_POOL_EXHAUSTED`; RTT ~20 ms
vs ~110 ms to us-central1).
The L4 is bundled into the `g2` machine type. **No `--accelerator` flag**. `g2-standard-4`
(4 vCPU) already passes preflight green: the L4's NVENC offloads OBS's encode, so preflight
detects the GPU and relaxes the CPU-core floor. `g2-standard-8` below is the roomier default.
Extra core headroom for a busy multi-feed event, not a preflight requirement:

```bash
gcloud compute instances create racecast-box \
  --zone=europe-west4-b \
  --machine-type=g2-standard-8 \
  --maintenance-policy=TERMINATE \
  --provisioning-model=STANDARD \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-type=pd-standard \
  --boot-disk-size=50GB
```

T4 fallback (needs the flag): `--machine-type=n1-standard-8
--accelerator=type=nvidia-tesla-t4,count=1` (e.g. `us-central1-a`). A create/start can hit
`ZONE_RESOURCE_POOL_EXHAUSTED`: retry across zones.

The `racecast@racecast-box` login (below) relies on **OS Login being off** so metadata SSH
keys create the `racecast` user. That is the GCP default. If your project/org **enforces**
OS Login (usernames are then derived from your Google identity, not `racecast`), either
turn it off for this box, add `--metadata enable-oslogin=FALSE` to the create above, or
keep OS Login and drive racecast through the service user with `sudo -iu racecast racecast
<cmd>` instead of the plain login.

## 2. Provision (once)

Connect **as `racecast`** (the `racecast@` prefix, first connect creates the user), then
run the script. **Manual, with output on screen (recommended for the first setup):**

```bash
gcloud compute scp tools/cloud/provision.sh racecast@racecast-box:~/ --zone=europe-west4-b
gcloud compute ssh racecast@racecast-box --zone=europe-west4-b
  $ sudo ./provision.sh        # idempotent: re-run after any red line
```

The script also copies `prepare-event.sh` into `~racecast/` for per-event prep (§4b); in startup-script mode where the file is absent, you can `scp` it up manually.

**Reproduction one-liner: unattended startup-script (any league, from scratch):**

```bash
gcloud compute instances create racecast-box ... \
  --metadata-from-file startup-script=tools/cloud/provision.sh
# watch the log:
gcloud compute instances get-serial-port-output racecast-box --zone=europe-west4-b
```

Optional env (never commit these):

- `TS_AUTHKEY`: a reusable/ephemeral **tagged** Tailscale pre-auth key for unattended
  join. The tailnet join is **required** (see below), not optional. This key only makes it
  unattended. Leave it unset for the single persistent box and complete the join in the
  browser.
- `RACECAST_TAG`: racecast release to install (default `latest` = latest **stable**).
  Set `RACECAST_TAG=preview-main` to install the current `main` preview build. Needed
  until the Linux `install-tools`/`install-apps` fixes reach a stable release.
  Apt-update-first (#408/#412) **and** the streamlink-venv (≥ 8.2.0) + obs-pipewire-audio
  plugin installs (#395). Without them a fresh box gets a too-old streamlink (every
  cookie'd YouTube feed aborts) and no Discord audio plugin. `latest` never picks a
  pre-release, so the preview is strictly opt-in. Cut it with `gh workflow run preview.yml
  --ref main` (rolling tag `preview-main`, re-pointed on each run).
- `RUSTDESK_PASSWORD`: the RustDesk password to set (never commit it). When unset,
  provision **generates** a strong random one; either way the first-boot oneshot applies it
  and writes the ID + password to `~racecast/rustdesk-access.txt`.
- `RUSTDESK_VERSION`: pin a RustDesk release (default in the script; bump if outdated).
- `PROVISION_REBOOT`: reboot at the end of provisioning to bring up the desktop session +
  finish RustDesk setup. **Default on**; set `PROVISION_REBOOT=0` to opt out (then reboot
  manually before connecting over RustDesk).

The script ends with a green/red verification block. A red line names the step to re-run.

**Tailscale join: required, not optional.** The tailnet is the box's trust boundary (the
relay's control port is never public), so step 10 **joins the box**: with `TS_AUTHKEY` it is
unattended; **run provision interactively** and step 10 executes `tailscale up` right there.
It prints a `https://login.tailscale.com/…` URL you open in your **laptop browser** to
approve the box, and provisioning **waits** until you do. Only a **detached/startup-script**
run (no terminal, no key) can't prompt: it prints the one command for you to run once.
`sudo tailscale up --ssh --hostname racecast-box`, and the verification block flags the box
as not-yet-joined until you complete it. Either way the box is not usable remotely until the
join is done.

## 3. First boot — reboot, then connect over RustDesk

- **Provision reboots automatically** at the end (default on; opt out with
  `PROVISION_REBOOT=0`) so the `racecast` **autologin desktop** comes up on `:0` (OBS +
  Discord autostart into it; RustDesk mirrors that display). The GPU driver itself loads
  without a reboot, but the desktop session needs one, so let the reboot happen, or if you
  opted out, reboot manually before connecting.
- **RustDesk is auto-configured**: no GUI password step. On that first boot the
  `racecast-rustdesk-setup` oneshot sets the password (generated by provision, or your
  `RUSTDESK_PASSWORD`), best-effort enables direct IP access, and writes the **ID +
  password** to `~racecast/rustdesk-access.txt`. Read it over SSH:
  ```bash
  gcloud compute ssh racecast@racecast-box --zone=… --command='cat ~/rustdesk-access.txt'
  ```
  Then connect from your laptop's RustDesk with that **ID + password** (or, over the
  tailnet, the box's **`100.x` IP** once "Enable direct IP access" is on. A one-click in
  Settings → Security if the CLI toggle didn't take). What you see is the `racecast`
  session on `:0`, the same one OBS runs in, so desktop, OBS and the install tree are all
  the one `racecast` user.

- **Event day is SSH-only: no RustDesk needed.** The autologin xfce session
  comes up at boot (as `racecast`), and `provision.sh` installs autostart entries so OBS +
  Discord launch with it. From your laptop, `gcloud compute ssh racecast@racecast-box …`
  then plain `racecast preflight` and `racecast event start`. `event start` also
  (re)launches OBS/Discord into the running session over SSH (it sets `DISPLAY=:0`;
  override with `RACECAST_DISPLAY`). RustDesk stays only for the one-time per-league OBS
  scene-collection import.

## 4. Onboard a league (once per league, then reuse)

Not part of `provision.sh`. This is the profile layer. The event tree lives directly in
`/home/racecast` (binary at `/home/racecast/racecast`, with `profiles/` + `runtime/`
alongside). Ship the league as a portable **profile bundle** and import it on the box.
Because you SSH in as `racecast`, the whole flow is plain commands:

```bash
# from your laptop, export the league to a portable bundle, copy it to the box:
racecast profile export <league> --out /tmp/<league>.zip                       # (on your laptop)
gcloud compute scp /tmp/<league>.zip racecast@racecast-box:~/ --zone=europe-west4-b
# on the box (logged in as racecast), import + activate + localize:
racecast profile import ~/<league>.zip
racecast profile use <league>
racecast setup            # localize the OBS scene collection for this profile
# then import the localized collection into OBS (GUI over RustDesk, once per league)
```

**Cookies live on the box** (Firefox is installed here): over RustDesk, sign in to YouTube
(and gated Twitch) in the box's Firefox with the dedicated racecast Google account, then
export them on the box over SSH. `racecast cookies firefox` (+ `racecast cookies twitch
firefox`). No scp from the laptop, and the cookies are created and used on the same
datacenter IP (no session-origin mismatch). The operator-facing walkthrough is the
**Remote producer (cloud GPU box)** wiki page.

**Discord voice-join (per league, once, a Developer-Portal step the scripts can't do).**
The box auto-joins the league's Discord voice channel so OBS captures the commentary
(`racecast discord join`, and the event-start auto-join). This drives the box's desktop
Discord over its RPC socket using the league's `DISCORD_CLIENT_ID`, and Discord grants the
restricted **`rpc` scope only to the app's OWNER or its App Testers**. The box's desktop
Discord is logged in as the shared **`gtracecast`** account, which does not own a league's
app: so on the league's Discord application (Developer Portal → the app → **App Testers**)
**add `gtracecast` as an App Tester** and accept the invite as `gtracecast`. Being only a
Discord **Team** member is NOT sufficient for the `rpc` scope. It must be App Testers.
Without it, `racecast discord join` fails with `discord: HTTP Error 400` (an `invalid_scope`
RPC error underneath). The consent popup then appears once in the box's Discord; afterwards
the token is cached (7-day refresh) and the auto-join is hands-free.

Switch between already-onboarded leagues with `racecast profile use <name>`.

## 4b. Prepare for an event (`prepare-event.sh`)

`provision.sh` drops `prepare-event.sh` into `~racecast/`. Before each event, SSH in as
`racecast` and run it with the league profile:

```bash
gcloud compute ssh racecast@racecast-box --zone=europe-west4-b
  $ ./prepare-event.sh <league>            # + --no-twitch / --no-speedtest / --no-update
```

It runs, in order: `racecast update` (with a **preview guard**, a deliberate
`preview-main` build is kept unless you confirm the downgrade to stable), `profile use`,
YouTube **and** Twitch cookie refresh, `graphics` / `media` / `brands`, `speedtest`, a
forced-clean relay (`relay stop` + `freeport --force`), and `preflight`. It stops at
**ready**: it never goes live. A closing readiness report lists any one-time manual
setup still missing (tailnet join, OBS scene-collection import, cookies, Discord token)
with the exact fix, and exits non-zero if a go-live prerequisite is missing. The tailnet
join, the OBS scene-collection import, or a failing preflight.

Go live afterwards from the browser Director Panel or `racecast event start`.

## 5. Cost control — stop between events

STOP the box after every event: a running GPU instance bills by the hour, a
stopped one only its boot disk. The tailnet IP is stable across stop/start, so
the box keeps its `100.x` address after a restart.

Use the **control wrappers**: `aws-box.sh` for the AWS box, `gcp-box.sh` for the
GCP box. Each takes `status` (default), `start`, `stop`, `ip`, and `ssh`, so you
never have to remember the instance id/zone:

```bash
tools/cloud/aws-box.sh status     # cloud state + type + public IP
tools/cloud/aws-box.sh start      # start, wait until running, print the SSH line
tools/cloud/aws-box.sh stop       # stop -> billing drops to the EBS boot disk
tools/cloud/aws-box.sh ssh        # ssh in as racecast over the tailnet

tools/cloud/gcp-box.sh status     # RUNNING | TERMINATED (= stopped) + type + external IP
tools/cloud/gcp-box.sh start      # gcloud start is synchronous (waits for RUNNING)
tools/cloud/gcp-box.sh stop
tools/cloud/gcp-box.sh ssh        # gcloud compute ssh racecast@racecast-box
```

Defaults target the current boxes (AWS `i-04d70428c19a484ef` in `eu-central-1`;
GCP `racecast-box` in `europe-west4-b`); override per box with the env vars
documented in each script's header (`AWS_BOX_*` / `GCP_BOX_*`). The raw CLI
equivalents remain:

```bash
aws  ec2 stop-instances  --instance-ids i-04d70428c19a484ef --region eu-central-1
gcloud compute instances stop racecast-box --zone europe-west4-b
```

## Confidence-building before GPU hours

The one genuinely GPU-specific unknown is "does X start on the T4 with no monitor". Everything
else is validatable without a GPU. De-risk in three tiers:

1. **CPU dry-run (pennies).** Run `provision.sh` on a cheap non-GPU VM (the Stage 0/1
   e2-micro). `has_nvidia_gpu()` auto-skips the driver + xorg steps, so the rest.
   Lightdm/autologin config, RustDesk + direct-IP over Tailscale, Firefox deb, `racecast
   install-tools`/`install-apps`, the verification block. Runs identically and catches
   the non-GPU bugs. Use `RACECAST_TAG=preview-main` so the *fixed* install code is what
   you test.
2. **Isolated GPU smoke test (~15 min on the GPU box).** Before any OBS/onboarding:
   `provision.sh` → reboot → check only the display lines of the verification block
   (`nvidia-smi` lists Xorg as a GPU process, `pgrep Xorg`, `DISPLAY=:0 glxinfo` renderer
   is the T4 not `llvmpipe`, RustDesk shows the xfce desktop). Green = the risky part is
   proven; only then invest in OBS setup + NVENC (#421).
3. **Fallback.** The live run's headless-X recipe (`nvidia-open` has no `nvidia-xconfig`,
   so `provision.sh` writes `/etc/X11/xorg.conf` by hand. Single 1920×1080, BusID from
   `lspci`) is proven. If X still won't start, feed a CustomEDID (fake a 1080p monitor).
   NVENC encoding is independent of the X display, so it is never at risk while the desktop
   display is being tuned.

## Notes

- `provision.sh` runs as root for the machine layer (driver, apt, sudoers) but installs
  racecast **straight into the `racecast` login user's home** (`/home/racecast`, user-owned,
  the binary at `/home/racecast/racecast`, no nested `racecast/` dir) and runs
  `install-tools`/`install-apps` **as that user**. So every event operation. Profile
  switch, cookie refresh, relay runtime writes, `install-tools --update`. Runs without
  `sudo`. The apt steps inside `install-apps` use the passwordless sudo the script set up.
  Because you SSH in **as `racecast`** (`gcloud compute ssh racecast@racecast-box`; OS Login
  is off so the guest agent creates that user from the metadata key), there is no second
  account and no `sudo -iu`. You simply are the owner of the tree.
- NVENC proof (that OBS uses the T4 encoder, not a silent x264 fallback) is the runbook's
  Appendix B checklist (#421).
