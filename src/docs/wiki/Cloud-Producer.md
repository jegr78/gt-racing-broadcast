# Remote producer (cloud GPU box)

Run the **producer station**: the relay, OBS, and the broadcast encode/upload, on an
on-demand **GCP GPU instance** instead of your home machine. You keep only your own
commentator stream and the browser [director panel](Run-an-event#the-director-panel-remote-control)
at home. This page is the **event-day operator guide**: start the box, get in, run the show,
send the report, stop the box.

> **One-time build** (create the VM, install the driver/desktop/toolchain) is a separate
> maintainer step: see `tools/cloud/README.md` and the spike runbook in the repo. This page
> assumes the box already exists and was provisioned.

## Home or cloud? (choosing per event)

The **default** is to produce at home, the relay, OBS and the broadcast upload run on a
producer's own machine ([Set up the broadcast PC](Set-up-the-broadcast-PC)). The cloud box is
an **offload**: it moves the heavy producer station off a home line when that line can't carry
it. **The director's job is identical either way**: arm the incoming feed, cut, the outgoing
feed auto-stops ([Director](Director#at-a-driver-change)). Two things decide per event:

**1. Platform.** YouTube feeds hit a **per-IP rate-limit on a datacenter IP**, two concurrent
pulls throttle within ~2 minutes (measured, issue #505); a **home/residential IP does not**. So:

- **YouTube-heavy** (a real two-feed splitscreen / POV on YouTube) → **produce at home**.
- **Twitch-heavy or single-feed** → **the cloud box is fine** (Twitch has no such throttle).

**2. Upload headroom**, the gating number when producing at home:

| Load | Direction | ~Rate |
|---|---|---|
| Program broadcast out | **up** | 6–8 Mbps |
| Your own commentary stream (only if you commentate) | **up** | ~5.5 Mbps |
| Each commentator feed pulled | down | ~5.5 Mbps |

Download is rarely the problem; **upload is.** Producing **and** self-commentating on one home
line is ~12–14 Mbps up, more than most asymmetric home uplinks. Ways out, in order: let
**someone else with a fat/symmetric line produce** (onboard them with [Profiles](Profiles)
`export`/`import` + `racecast cookies`), **don't self-commentate** that event, or **cut feed
count/bitrate**. `racecast speedtest` and the preflight bandwidth check give the raw numbers,
do the arithmetic above against them.

## The model

- **One long-lived instance**, reused for every event by switching
  [league profiles](Profiles) (`racecast profile use <name>`). **Stop it between events**:
  you pay for the GPU only while it runs; the **Tailscale IP stays stable** across
  stop/start.
- **Event day is SSH-only.** The box boots into an autologin desktop and launches OBS +
  Discord automatically; `racecast event start` drives them over SSH. You do **not** need a
  remote-desktop connection for a normal event.
- **RustDesk is the fallback**, only for GUI work: the one-time per-league OBS
  scene-collection import, or hands-on troubleshooting.

The running example below uses the instance name **`racecast-box`** in zone
**`europe-west4-c`**: substitute your own.

## 1. Start / stop the box

**gcloud (Terminal):**

```bash
gcloud compute instances start racecast-box --zone=europe-west4-c   # before the event
gcloud compute instances stop  racecast-box --zone=europe-west4-c   # after: stops GPU billing
```

**Web Console:** Google Cloud Console → **Compute Engine → VM instances** → tick
`racecast-box` → **Start / Resume** or **Stop** (also in the row's **⋮** menu).

Give the box ~1–2 minutes after start to reach the desktop before you SSH in. The tailnet IP
is unchanged, so all your saved `http://100.x…` bookmarks keep working.

## 2. SSH into the box (as `racecast`)

Always connect **as the `racecast` user** (the `racecast@` prefix), the event stack lives
in its home and runs as it, so this is the only login you need. On the very first connect
the GCP guest agent creates the `racecast` user from your SSH key.

**gcloud (Terminal), simplest, keys handled for you:**

```bash
gcloud compute ssh racecast@racecast-box --zone=europe-west4-c
```

**Web Console:** on the VM instances list, click the **SSH** button, this logs in as your
own account, so it is only for machine-level checks; for racecast commands use the gcloud
`racecast@` login above (or `sudo -iu racecast` from the browser shell).

**Plain Terminal (direct IP or over Tailscale):** find the external IP once,

```bash
gcloud compute instances describe racecast-box --zone=europe-west4-c \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

then `ssh racecast@<EXTERNAL_IP>` (or `ssh racecast@100.x.y.z` over the tailnet once the box
has joined: see §5).

> **First connection: go passwordless.** Install your public key once so `ssh` **and**
> `scp` never prompt again:
> ```bash
> ssh-copy-id racecast@<EXTERNAL_IP>
> ```
> After this every later `ssh`/`scp` to the box is key-based and silent. The **gcloud**
> method already manages keys for you, so `ssh-copy-id` is only for the plain-Terminal path.

> **Log in as `racecast`.** The box runs the event stack as a dedicated **`racecast`**
> login user (home `/home/racecast`: binary, `profiles/`, `runtime/`, cookies). You connect
> **directly as that user**, `gcloud compute ssh racecast@racecast-box`, so every command
> below is plain `racecast <cmd>`, no `sudo`. (On first connect the GCP guest agent creates
> the `racecast` user from your SSH key.)

## 3. Send files to the box

You mainly copy in a **league profile** the first time you onboard it (cookies are handled
on the box: see §6, no upload). Ship it as a **portable profile bundle** and import it,
because you SSH in as `racecast`, it drops straight into the event tree:

**gcloud (Terminal):**

```bash
racecast profile export <league> --out /tmp/<league>.zip          # on your laptop
gcloud compute scp /tmp/<league>.zip racecast@racecast-box:~/ --zone=europe-west4-c
gcloud compute ssh racecast@racecast-box --zone=europe-west4-c --command="\
  racecast profile import ~/<league>.zip && racecast profile use <league>"
```

**Plain Terminal (after `ssh-copy-id`, no password):**

```bash
scp /tmp/<league>.zip racecast@<EXTERNAL_IP>:~/
ssh racecast@<EXTERNAL_IP> 'racecast profile import ~/<league>.zip'
```

**Web Console:** in the browser **SSH** window, use the **⚙ gear → Upload file** menu to
drop the bundle into the `racecast` home, then `racecast profile import ~/<league>.zip`.

## 4. Run the event (SSH-only)

From your laptop, once the box is up and you are SSHed in **as `racecast`**
(`gcloud compute ssh racecast@racecast-box`):

```bash
racecast profile use <league>     # (or let prepare-event.sh do the whole prep: below)
./prepare-event.sh <league>       # update (preview-guarded) · cookies (YouTube+Twitch) ·
                                  # graphics · media · brands · speedtest · fresh relay · preflight
racecast event start              # go live (relay + OBS + Discord): prepare-event.sh does NOT
```

`./prepare-event.sh <league>` orchestrates all the per-event prep steps before go-live:
it runs `racecast update` (with a **preview guard**, a deliberate `preview-main` build
is kept unless you confirm the downgrade to stable), `profile use`, YouTube **and** Twitch
cookie refresh (pass `--no-twitch` to skip Twitch), graphics/media/brands refresh,
`speedtest` (pass `--no-speedtest` to skip), a fresh relay (stop + free feed ports), and
`preflight`. It stops at **ready**, it does NOT go live. A closing readiness report exits
non-zero if a go-live prerequisite is missing, the tailnet join, the OBS scene-collection
import, or a failing preflight.

The asset refreshes pull the current look from the league's Google Sheet into
`runtime/<profile>/{graphics,media,brands}/` on the box, so a graphic, clip or brand-logo
edit made since the last event is picked up. They are safe to re-run and cost nothing when
nothing changed. This mirrors the same-day prep in [Run an event](Run-an-event#before-you-go-live).

`racecast event start` then (re)launches OBS and Discord **into the running desktop session**
over SSH (it sets `DISPLAY=:0`; override with `RACECAST_DISPLAY`). From there you drive
everything from the browser **[Director Panel](Run-an-event#the-director-panel-remote-control)**,
including starting and stopping each **[broadcast Part](Run-an-event#broadcast-parts-director-panel)**.
No RustDesk needed.

Nobody watches the box itself during the event, so the Director Panel and Discord are the
only places a problem shows. If the program starts falling behind the commentators'
streams, follow [The picture falls behind live](If-something-goes-wrong#the-picture-falls-behind-live):
what the panel shows, what reaches Discord, and what the director can do.

When the event is over:

```bash
racecast event stop               # stops the racecast services (GUI apps keep running)
gcloud compute instances stop racecast-box --zone=europe-west4-c   # then stop the box (§1)
```

## 5. Join the tailnet

Joining the tailnet is **required, not optional**: it is the trust boundary that lets your
laptop reach the [Control Center](Control-Center) and `/console/panel` privately (the relay's
control port is never exposed publicly). Provisioning does this at **step 10**, the box is
not usable remotely until it is done.

- **Interactive provisioning (the single persistent box):** step 10 runs `tailscale up` for
  you, it prints a `https://login.tailscale.com/…` login URL and **waits**; open it in your
  **laptop** browser and approve the box. (If you ever need to redo it: `sudo tailscale up
  --ssh --hostname racecast-box`.)
- **Unattended:** provisioning with a `TS_AUTHKEY` (a reusable/ephemeral **tagged** pre-auth
  key) joins the box automatically, no browser step. A **detached/startup-script** run without
  a key can't prompt: it prints that one command for you to run once.

Verify and use it:

```bash
racecast tailscale status         # confirms the 100.x address
```

Then open the Control Center at **`http://100.x.y.z:8089`** and hand out `/console` links as
usual. To let directors/commentators help **without a Tailscale account**, publish only the
role-gated `/console` page over the Funnel: see
[Remote access & the Funnel](Remote-access).

## 6. Feed cookies: on the box, not uploaded

YouTube (and gated Twitch) feeds need a signed-in browser session. Because **Firefox runs on
the box**, you sign in there once and export locally, nothing is copied from your laptop:

1. Connect once over **RustDesk** (§7) and, in the box's Firefox, sign in to **YouTube** (and
   **Twitch** if the league uses gated Twitch feeds) with your **dedicated racecast Google
   account**, one account covers both.
2. Whenever cookies need refreshing (before an event, or after they expire), export them
   **on the box** over SSH:
   ```bash
   racecast cookies firefox          # YouTube
   racecast cookies twitch firefox   # only for gated Twitch feeds
   ```

Because the cookies are both created **and** used on the box's datacenter IP, there is no
"home-IP session used from a datacenter IP" mismatch to trip the bot-check, an improvement
over the old export-locally-and-upload flow.

## 7. RustDesk: the fallback (GUI work only)

You only need a remote **desktop** for two things: the **one-time OBS scene-collection
import** per league, and hands-on **troubleshooting**. Everything else is SSH.

RustDesk mirrors the display on `:0`, which is the **`racecast` autologin session**, the
same session OBS runs in: so the remote desktop, OBS and the install tree are all the one
`racecast` user (no user mismatch to trip over).

- **RustDesk is auto-configured**: provision sets the password and prints the credentials.
  After the first boot, read them over SSH: `cat ~/rustdesk-access.txt` (**ID + password**,
  written by the `racecast-rustdesk-setup` first-boot oneshot; override the password with
  `RUSTDESK_PASSWORD` at provision time). No GUI password step.
- Connect from your laptop's RustDesk with that **ID + password**. Over the tailnet you can
  instead use the box's **`100.x` IP** (direct IP, no public relay) once **Settings →
  Security → "Enable direct IP access"** is on, a one-click if the scripted toggle didn't
  take on your RustDesk build.
- Provisioning **auto-reboots** at the end (default on; `PROVISION_REBOOT=0` opts out) to
  start the autologin X session RustDesk shows. If the desktop is black, reboot once more.

Then, per league, import the localized scene collection into OBS once:

```bash
racecast setup                    # writes runtime/<profile>/GT_Racing_Endurance.import.json
# in the OBS GUI over RustDesk: Scene Collection → Import → that file
```

On a multi-league box you only import each league's collection once; from then on
`racecast event start` auto-switches OBS to the active profile's collection at
bring-up, so a previous event's collection can't linger.
(`RACECAST_OBS_COLLECTION_SWITCH=0` disables it; `racecast obs collection set`
switches manually.)

## Discord voice audio (auto-join)

The producer box captures interview/commentary audio by having its **Discord desktop
client** sit in the league's **voice channel** (OBS grabs that audio via the PipeWire
plugin). racecast can join that voice channel for you, no clicking around in Discord over
RustDesk.

**One-time setup (per league):**

1. On the league's Discord application (the same app as the `/console` OAuth,
   `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` in `profile.env`), register
   **`http://localhost`** as an OAuth2 redirect (Discord Developer Portal → your app →
   OAuth2 → Redirects). This is the only manual Discord-side step.
2. Set the voice channel: either the Sheet **`Configuration`** tab's **`Discord Voice`**
   cell (a `https://discord.com/channels/<guild>/<channel>` link, editable without a file
   change: this wins when set), or `DISCORD_VOICE_URL` in `profile.env` as the fallback.
3. First run, on the box, do it once interactively so you can approve the consent:
   `racecast discord join` → a one-time "authorize" popup appears in the box's Discord
   (over RustDesk) → click **Authorize**. The token is cached, so every later join is
   silent and hands-free.

**Every event after that:** `racecast event start` **auto-joins** the voice channel
(default on). Disable with `RACECAST_DISCORD_AUTOJOIN=0` in the machine `.env`.

**Manual control** any time. CLI `racecast discord join` / `racecast discord leave` /
`racecast discord status`, or the Control Center **Apps → Discord → Join voice / Leave
voice** buttons.

## 8. Post-event report → Discord

After the event, generate the report and send it to the league Discord:

```bash
racecast report                   # build the HTML report into runtime/<profile>/reports/
racecast report send              # attach the newest report to the league Discord
#   racecast report send <FILE>   # or send a specific report file
```

`report send` needs **`DISCORD_WEBHOOK_URL`** in the active league's `profile.env`. You can
also generate and send it from the **Control Center → Post-Event Report** card. Full details:
[Health Monitor → Post-Event Report](Health-Monitor#post-event-report).

## Quick reference

| Task | gcloud | Web Console | Plain Terminal |
|---|---|---|---|
| Start / stop | `gcloud compute instances start\|stop …` | Compute Engine → Start/Stop | — |
| SSH | `gcloud compute ssh racecast@racecast-box …` | **SSH** button (browser) | `ssh racecast@<IP>` (after `ssh-copy-id`) |
| Send files | `gcloud compute scp <league>.zip racecast@…:~/` (then import) | SSH window → **⚙ Upload file** | `scp <league>.zip racecast@<IP>:~/` |
| Remote desktop | — | — | RustDesk → `100.x` IP (fallback) |
| Discord voice | — | — | `racecast discord join\|leave` |

See also: [Run an event](Run-an-event) · [Remote access & the Funnel](Remote-access) ·
[Set up the broadcast PC](Set-up-the-broadcast-PC) · [League profiles](Profiles).
