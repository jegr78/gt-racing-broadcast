# Arch Linux: the CachyOS example

A supplement to [Set up the broadcast PC](Set-up-the-broadcast-PC) for Arch. Follow
that page as usual: this one replaces the two install steps and adds the handful of
things that work differently.

Every command and every claim on this page was run on a working broadcast machine
running **CachyOS** (KDE Plasma, x86-64). That is deliberate: Arch derivatives differ
in which repositories they enable and how current they keep them, so a recipe that is
merely *plausible* elsewhere is worth little on a machine you have to trust during a
live show. Read this as "how it was actually done", not as a guarantee for every
Arch-based distro. On another derivative the shape will be the same; the package
names and repositories are worth re-checking.

## The short version

| Step | On Arch |
|---|---|
| `racecast install-tools` | **does nothing**: install the four tools with pacman instead |
| `racecast install-apps` | **does nothing**: install OBS, Tailscale and Discord with pacman |
| OBS Studio | needs a **different package**, or the HUD and timer stay black |
| Discord audio into OBS | a **separate plugin**, named differently than on Windows |
| Bitfocus Companion | **not packaged at all**: install it by hand (below) |
| `racecast obs-browser` | **don't run it**, it builds CEF from source using `apt` |
| Keeping it updated | `pacman -Syu` **misses AUR packages**, the audio plugin needs `yay` |
| Everything else | works unchanged: relay, Control Center, Console, Funnel, profiles |

`racecast install-tools` and `install-apps` only know winget, Homebrew and apt. On
Arch they exit with *"No supported package manager found"* and print an apt-based
guide that does not apply. Nothing is broken, the tools simply aren't wired up for
pacman yet.

## 1. Command-line tools

```bash
sudo pacman -S --needed yt-dlp streamlink ffmpeg deno
```

All four are in the official **`extra`** repository and current. This is the one
place where Arch is *easier* than Debian or Ubuntu: apt's `streamlink` is too old
for racecast (it needs **8.2.0 or newer** for `--http-cookies-file`), which is why
`install-tools` builds it into a virtualenv there. Arch ships 8.4.0, so the plain
package is enough.

Keep them updated with the rest of the system, but read
[Rolling release and event days](#rolling-release-and-event-days) first.

**You should now see:** `streamlink --version`, `yt-dlp --version`, `ffmpeg -version`
and `deno --version` each print a version.

## 2. The apps

```bash
sudo pacman -S --needed tailscale discord
sudo systemctl enable --now tailscaled
```

OBS needs a moment of attention:

### OBS Studio must be the browser-capable build

```bash
sudo pacman -S obs-studio-browser        # NOT obs-studio
```

The plain `obs-studio` package is built **without Chromium (CEF)**. It works as a
video mixer, but every Browser Source is missing, and the relay serves the
[HUD and the race timer](HUD-Overlays) as Browser Sources. The symptom is not an
error message: the scenes import fine, the sources are listed, and the overlay areas
are simply **black**. This is the single most likely reason a first setup on Arch
looks finished and isn't.

`obs-studio-browser` is the same OBS with CEF included. On CachyOS it comes from that
distribution's own repository and `provides` `obs-studio`, so it slots in as a
drop-in replacement.

Check that it took:

```bash
pacman -Ql obs-studio-browser | grep obs-browser.so
```

> **Do not run `racecast obs-browser` here.** That command builds CEF from source and
> drives `dpkg-query` and `apt-get` to do it, it is for ARM64 Debian/Ubuntu, where no
> prebuilt browser plugin exists. On Arch the fix is the package swap above.

### Discord audio needs a plugin, and it has a different name

```bash
yay -S obs-pipewire-audio-capture        # from the AUR
```

Windows and macOS have **Desktop App Audio Capture** built into OBS. Linux doesn't.
This plugin is the equivalent: in OBS's source list it appears as **Application Audio
Capture (PipeWire)**: internally `pipewire_audio_application_capture`. Point it at
Discord to get the interview audio into the broadcast.

Note that `obs-pipewire-audio-capture` depends on `obs-studio`, which
`obs-studio-browser` satisfies. Install OBS first, and pacman will not try to pull the
CEF-less package back in.

**You should now see:** in OBS, **Sources → +** offers both *Browser* and
*Application Audio Capture (PipeWire)*.

### `yay` and the AUR: one package, one lasting consequence

That audio plugin is the **only** part of a racecast station that comes from the
**AUR** (the Arch User Repository: community build recipes, not an official
repository). `yay` is the helper that fetches and builds them; CachyOS ships it
preinstalled. Everything else on this page is an official package.

List what you have from outside the repositories at any time:

```bash
pacman -Qm
```

The consequence outlives the install, and it is the part worth remembering:

```bash
pacman -Syu     # updates repository packages, and NOTHING from the AUR
yay -Qua        # what AUR updates are pending?
yay -Sua        # apply them (rebuilds from source)
```

`pacman` has no knowledge of AUR packages at all. On this machine a Chrome update
had been waiting for days: `yay -Qua` listed it, `pacman -Qu` did not mention it,
so a diligent `pacman -Syu` leaves that plugin frozen in place indefinitely. That is
mostly harmless, but do not mistake a clean `pacman -Syu` for "everything is current".

The good news for event days: **`IgnorePkg` covers AUR packages too.** Adding a
package to the list in `pacman.conf` makes `yay` drop it from `yay -Qua` and refuse to
upgrade it: verified by experiment, not assumed. One freeze list protects the whole
station. See [Rolling release and event days](#rolling-release-and-event-days).

A rolling release moves `ffmpeg`, `yt-dlp`, `streamlink` and `deno` in bulk, and all
four sit in the feed path. After an upgrade that touched any of them, run the
[smoke test](Smoke-Test): it stands up a throwaway event against live streams and
tells you whether the broadcast core still works, before an event day does.

## 3. Bitfocus Companion (manual)

Companion is in **no** Arch repository and not in the AUR under a usable name
(`companion-satellite` is a different product, it connects local Stream Decks to a
*remote* Companion). This is the genuine gap: it has to be installed by hand.

racecast expects the **companion-pi** layout on Linux, because that is what
`racecast companion start/stop` controls. Reproduce it:

```bash
# 1. Find a build: Bitfocus has an API, there is no stable download URL
curl -s 'https://api.bitfocus.io/v1/product/companion/packages?branch=stable&limit=6&target=linux-tgz'
#    -> packages[].uri

# 2. Unpack into the layout racecast expects
sudo useradd --system --create-home --home-dir /home/companion \
             --shell /usr/bin/nologin companion
sudo mkdir -p /opt/companion /opt/companion-module-dev
sudo tar -xzf companion-*.tar.gz --strip-components=2 -C /opt/companion \
         --wildcards '*/resources'
sudo chown -R companion:companion /opt/companion
```

Then write the service unit yourself: companion-pi's own unit points at a launch
script that does not exist outside its image:

```ini
# /etc/systemd/system/companion.service
[Unit]
Description=Bitfocus Companion
After=network-online.target

[Service]
User=companion
WorkingDirectory=/opt/companion
ExecStart=/opt/companion/node-runtimes/main/bin/node /opt/companion/main.js \
          --admin-address 127.0.0.1 --extra-module-path /opt/companion-module-dev
Restart=on-failure
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

Finally hand control to racecast, **in this order**:

```bash
sudo systemctl daemon-reload
racecast companion enable-control      # FIRST: installs the bind drop-in + root helper
racecast companion start               # THEN: binds to the Tailscale IP and starts
```

> **The order matters.** `enable-control` rewrites Companion's bind address back to
> the default `127.0.0.1`. Running it *after* `companion start` silently pulls
> Companion off the tailnet, and the button board becomes unreachable from the
> director's tablet, which looks exactly like a firewall problem and isn't one.
> Re-run `enable-control` only when the unit itself changes.

Two details that cost time if you don't know them:

- **Don't install an older Companion than the machine your button config came from.**
  Companion migrates configuration forward only.
- `node-runtimes/main` is a **symlink**. The singular `node-runtime` of older versions
  is gone in 5.x; racecast handles both.

Then import the buttons as usual: [Companion](Companion).

## 4. The bandwidth test

```bash
racecast speedtest
```

If this fails with a confusing *"no internet?"*, check for a name collision first:

```bash
pacman -Qo /usr/bin/speedtest
```

The distro package **`speedtest-cli`** installs an unrelated Python tool under the
same name `speedtest`. racecast finds it on the PATH, reports *"already installed"*,
and then fails when the expected Ookla options aren't there. Remove `speedtest-cli`
and put the pinned Ookla CLI from [speedtest.net/apps/cli](https://www.speedtest.net/apps/cli)
in racecast's managed `runtime/bin/` instead.

This test is optional: it only feeds the bandwidth warning in
[preflight](Set-up-the-broadcast-PC#9-pre-flight-check).

## Rolling release and event days

Arch updates continuously. A broadcast wants the opposite: the exact set of versions
you last tested. A single `yt-dlp` bump has already cost one live broadcast.

The rule from [Run an event](Run-an-event), **never update on an event day**, needs
a little help here, because a routine `pacman -Syu` will happily replace your whole
streaming chain. Pin it with pacman's `IgnorePkg` in the `[options]` section of
`/etc/pacman.conf` before an event:

```ini
IgnorePkg = yt-dlp streamlink ffmpeg deno obs-studio-browser obs-pipewire-audio-capture tailscale
```

Remove the line afterwards, update, then validate with `racecast preflight` and a
short test stream before the next show.

Before the show, confirm the freeze actually took: with **both** tools, because they
answer different questions:

```bash
pacman-conf IgnorePkg    # what pacman really parsed (see the trap below)
yay -Qua                 # pending AUR updates, nothing racecast-relevant may be listed
```

Three things to know about `IgnorePkg`:

- It must be **inside `[options]`**. Appended at the end of the file it lands in
  `[multilib]`, where pacman ignores it *and says nothing*. Confirm with
  `pacman-conf IgnorePkg`, which prints what pacman actually parsed.
- Never `pacman -Sy` followed by `pacman -S <pkg>`. That mixes a fresh package list
  with an old system and links new packages against libraries you don't have.
  Only ever `pacman -Syu`.
- It **does** reach the AUR: `yay` reads the same `pacman.conf` and will skip an
  ignored package. One list covers repository and AUR packages alike, but only
  packages you actually named, so keep `obs-pipewire-audio-capture` in it.

Arch's saving grace is that recovery is cheap: with `snapper` (default on CachyOS)
every pacman run leaves a snapshot, and on a `limine`/`grub` setup with snapshot
integration you can boot the state from before the update straight from the boot menu.

## Things that bit us

Not Arch-specific, but they surfaced here first and cost the most time.

**An old Intel GPU may pick the wrong VAAPI driver.** libva defaults to `iHD`, which
only supports Broadwell and newer. On Haswell and older it fails with
`iHD_drv_video.so init failed`. VAAPI encoding in OBS is dead and screen sharing
shows black. Pin the right one in `/etc/environment`:

```bash
LIBVA_DRIVER_NAME=i965      # Haswell and older; leave unset on Broadwell+
```

systemd services do **not** read `/etc/environment`, so a remote-desktop service needs
the same value as a drop-in of its own.

Also worth measuring rather than assuming: on that same hardware, **x264 `veryfast`
beat VAAPI** (2.69× vs 1.97× realtime at 1080p60). The i965 path needs a CPU-side
format conversion, and a 2014 encode unit is slow. Test your own box before trusting
hardware encoding.

**Wayland blocks unattended screen sharing.** Every screen capture asks for
confirmation *on the machine*. On a box with no monitor nobody can click it, and
remote desktop shows a black screen forever. If you run the machine headless, use an
X11 session.

**Verify exported YouTube cookies functionally, not by age.** racecast reports cookie
freshness from the file's timestamp, and a file can look complete: every cookie
name present, expiry a year out: while the session has been invalidated
server-side. One request tells you the truth:

```bash
curl -s -A "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0" \
     -b runtime/yt-cookies.txt https://www.youtube.com/ \
  | grep -oE 'LOGGED_IN..(true|false)'
```

`true` means the session works. If it says `false` right after a successful export,
check which Firefox profile was read: `yt-dlp` picks the one marked `Default=1` in
`profiles.ini`, which is not always the profile you actually browse with: on this
machine it pointed at an empty leftover profile, so every export produced a
logged-out cookie file that looked perfectly healthy. On CachyOS that file lives
under `~/.config/mozilla/firefox`, not `~/.mozilla/firefox`.

## Checking your work

```bash
racecast preflight
```

Preflight is package-manager agnostic: it checks for working binaries, not for how
they were installed, so it is the right final gate on Arch too. It names the exact fix
for anything it finds; the apt-flavoured suggestions in its output are the only part
you should translate to pacman yourself.

If something still misbehaves, [If something goes wrong](If-something-goes-wrong) is
the general troubleshooting page.
