#!/usr/bin/env python3
"""provision.py: one-shot machine-layer provisioning for a GCP GPU box (Ubuntu 24.04)
for the racecast cloud-producer spike (#395).

Installs everything racecast does NOT cover: NVIDIA driver, xfce desktop,
Firefox (deb, not snap), RustDesk, passwordless sudo, Tailscale join, then delegates
the toolchain + applications to the racecast binary (install-tools / install-apps).

Idempotent: every step is existence-/stamp-gated; safe to re-run after a failure.
Runs as root: `sudo ./provision.py`, or as a GCP startup-script (instance metadata).
Self-contained (stdlib only, no repo imports) so the startup-script copy runs on its own.

Optional environment:
  TS_AUTHKEY        Tailscale reusable/ephemeral pre-auth key for unattended join.
                    NEVER commit this. The tailnet join is REQUIRED (step 10): with a key
                    it is unattended; run interactively without a key and step 10 performs
                    the browser-auth join (prints a login URL to approve in your laptop
                    browser, and WAITS); only a non-interactive run without a key defers it
                    to a one-line command the operator must run once.
  RACECAST_TAG      racecast release tag to install (default: latest = latest STABLE
                    release). Set to `preview-main` to install the current main
                    preview build: needed until the Linux install-tools/install-apps
                    fixes land in a stable release: apt-update-first (#408/#412) AND
                    the streamlink-venv >=8.2.0 + obs-pipewire-audio plugin installs
                    (#395). Without them a fresh box gets a too-old streamlink (every
                    cookie'd YouTube feed aborts) and no Discord audio plugin.
                    `latest` never selects a pre-release, so this is opt-in.
  RACECAST_USER     the event user to create and target (default: racecast).
  RUSTDESK_VERSION  RustDesk release to install (default: pinned below; check for newer).
  RUSTDESK_PASSWORD RustDesk password to apply on first boot (default: generated).
  PROVISION_REBOOT  0 = skip the closing reboot (default: reboot).

NOT handled here: per-league onboarding the operator does once per league:
  racecast profiles, cookies, `racecast setup`, OBS scene import, RustDesk password.
"""
import os, platform, re, secrets, shutil, string, subprocess, sys, time

RUSTDESK_DEFAULT_VERSION = "1.3.8"
RACECAST_REPO = "jegr78/gt-racing-broadcast"
DEVNULL = subprocess.DEVNULL
QUIET = {"stdout": DEVNULL, "stderr": DEVNULL}

OBS_LAUNCH = """#!/bin/sh
# Managed by racecast provision.py. Clear stale OBS crash sentinels so a hard-killed
# session (cloud instance stop/reboot) never triggers OBS's "did not shut down properly /
# Run in Safe Mode?" prompt on this unattended autologin box (Safe Mode also disables
# obs-websocket, which the relay drives).
rm -f "$HOME/.config/obs-studio/.sentinel/"run_* 2>/dev/null || true
exec obs "$@"
"""

DISCORD_LAUNCH = """#!/bin/sh
# Managed by racecast provision.py. Start Discord with the plaintext password store
# instead of the GNOME keyring: a passwordless-autologin account cannot auto-unlock the
# keyring, so the keyring-backed store pops an "Unlock Keyring" dialog on every start.
# --password-store=basic (a Chromium/Electron flag Discord honors) sidesteps it. The box
# is single-user + tailnet-only, so plaintext app-token storage is an acceptable trade
# for zero interactive prompts.
exec discord --password-store=basic "$@"
"""

RUSTDESK_HELPER = r"""#!/usr/bin/env bash
# Managed by racecast provision.py. Configures RustDesk once the desktop session is up.
set -u
USER_NAME="__USER__"
uid="$(id -u "$USER_NAME" 2>/dev/null)" || exit 0
run_as_user() { sudo -u "$USER_NAME" DISPLAY=:0 XDG_RUNTIME_DIR="/run/user/$uid" "$@"; }
# Wait for the rustdesk server (spawned in the user session) to answer with an ID.
rid=""
for _ in $(seq 1 30); do
  rid="$(run_as_user rustdesk --get-id 2>/dev/null | tr -dc '0-9')"
  [ -n "$rid" ] && break
  sleep 2
done
pw="$(cat /etc/racecast/rustdesk-password 2>/dev/null)"
[ -n "$pw" ] && rustdesk --password "$pw" >/dev/null 2>&1
# Enable direct IP access over the tailnet. The `--option` call is best-effort and did
# NOT stick on some builds, so ALSO write direct-server into the config files directly,
# with the service briefly stopped so rustdesk doesn't overwrite the edit on exit. This
# makes the box reachable via its STABLE tailnet IP ("direct IP", port 21118). No
# dependence on RustDesk's public rendezvous, and immune to the RustDesk ID drifting
# across a stop/start. (rustdesk may run its --server as the display-manager user, so edit
# root's, the user's, AND lightdm's config. Whichever exist.)
run_as_user rustdesk --option direct-server Y >/dev/null 2>&1 || true
systemctl stop rustdesk >/dev/null 2>&1 || true
for cfg in "/root/.config/rustdesk/RustDesk2.toml" \
           "/home/$USER_NAME/.config/rustdesk/RustDesk2.toml" \
           "/var/lib/lightdm/.config/rustdesk/RustDesk2.toml"; do
  [ -f "$cfg" ] || continue
  grep -q 'direct-server' "$cfg" && continue
  # Insert `direct-server = 'Y'` directly under the [options] table (append the table if
  # absent). awk (not sed's GNU-only `a`) so it is portable + the value's single quotes
  # ride in a -v var instead of shell-escaping. Atomic rewrite via a temp file.
  awk -v line="direct-server = 'Y'" '
    /^\[options\]/ && !ins { print; print line; ins=1; next }
    { print }
    END { if (!ins) { print ""; print "[options]"; print line } }
  ' "$cfg" > "$cfg.racecast-tmp" && mv "$cfg.racecast-tmp" "$cfg"
done
systemctl start rustdesk >/dev/null 2>&1 || true
# Re-read the tailnet IP (it may only come up after the join); retry briefly.
ip=""
for _ in $(seq 1 10); do
  ip="$(tailscale ip -4 2>/dev/null | head -1)"
  [ -n "$ip" ] && break
  sleep 2
done
out="/home/$USER_NAME/rustdesk-access.txt"
{
  echo "RustDesk access for this box (user: $USER_NAME). Generated on first boot."
  echo "  ID:         ${rid:-<run: rustdesk --get-id>}"
  echo "  Password:   ${pw:-<unset. Set /etc/racecast/rustdesk-password + re-run>}"
  echo "  Direct IP:  ${ip:-<tailscale ip -4>}   (tailnet, direct IP access enabled, port 21118)"
  echo
  echo "Connect from your laptop RustDesk either way:"
  echo "  - Direct IP (recommended, tailnet-only): enter the Direct IP above + password"
  echo "  - By ID: enter the ID above + password  (the ID can change across a stop/start; the Direct IP stays stable)"
} > "$out"
chown "$USER_NAME:$(id -gn "$USER_NAME")" "$out" 2>/dev/null || true
chmod 0600 "$out" 2>/dev/null || true
install -d /var/lib/racecast; : > /var/lib/racecast/rustdesk-configured
"""

RUSTDESK_UNIT = """[Unit]
Description=racecast: configure RustDesk (password + direct IP) once the desktop is up
After=graphical.target
Wants=graphical.target
ConditionPathExists=!/var/lib/racecast/rustdesk-configured

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/racecast-rustdesk-setup
RemainAfterExit=yes

[Install]
WantedBy=graphical.target
"""

MOZILLA_LIST = ("deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] "
                "https://packages.mozilla.org/apt mozilla main\n")
MOZILLA_PIN = "Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n"
OBS_BROWSER_SYSTEM = ("/usr/lib/x86_64-linux-gnu/obs-plugins/obs-browser.so", "/usr/lib/obs-plugins/obs-browser.so")


class Host:
    """Process and filesystem access for the real run; the tests substitute a recorder."""

    def which(self, name):
        return shutil.which(name)

    def realpath(self, path):
        return os.path.realpath(path)

    def isfile(self, path):
        return os.path.isfile(path)

    def exists(self, path):
        return os.path.exists(path)

    def isexec(self, path):
        return os.access(path, os.X_OK)

    def size(self, path):
        """Size in bytes, or None when the path does not exist (bash -s is size > 0)."""
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def read_bytes(self, path):
        """File content, or None when it cannot be read."""
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    def write(self, path, text):
        """bash `> path`: create or truncate; a failed redirection ends the script like set -e."""
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as e:
            print(f"provision.py: {path}: {e.strerror}", file=sys.stderr)
            sys.exit(1)

    def isatty(self):
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except (ValueError, OSError):
            return False

    def sleep(self, seconds):
        time.sleep(seconds)

    def run(self, argv, stdout=None, stderr=None, env=None):
        """Run argv with inherited stdio unless redirected; return its exit status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            return subprocess.run(argv, stdout=stdout, stderr=stderr, env=env).returncode
        except FileNotFoundError:
            if stderr is None:
                print(f"{argv[0]}: command not found", file=sys.stderr)
            return 127

    def capture(self, argv, stderr=None, env=None):
        """bash $(argv): (exit status, stdout without trailing newlines)."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=stderr, env=env,
                               text=True, errors="replace")
        except FileNotFoundError:
            return 127, ""
        return p.returncode, p.stdout.rstrip("\n")


def log(msg):
    print(f"\n\033[1;34m==>\033[0m {msg}")


def ok(msg):
    print(f"  \033[1;32mOK\033[0m  {msg}")


def warn(msg):
    print(f"  \033[1;33m!!\033[0m  {msg}")


def strict(rc):
    """bash set -e: a failed command ends the script with its status."""
    if rc:
        sys.exit(rc)


def first_line(text):
    return text.split("\n", 1)[0]


def home_field(passwd_line):
    """`cut -d: -f6` of a passwd entry."""
    parts = first_line(passwd_line).split(":")
    return parts[5] if len(parts) > 5 else (parts[0] if len(parts) == 1 else "")


def home_of(h, user, check=True):
    """`$(getent passwd user | cut -d: -f6)`; check=True is an assignment under set -e + pipefail."""
    rc, out = h.capture(["getent", "passwd", user])
    if check:
        strict(rc)
    return home_field(out)


def group_of(h, user, check=True):
    rc, out = h.capture(["id", "-gn", user])
    if check:
        strict(rc)
    return out


def version_of(h, argv):
    """`$(argv 2>/dev/null | head -1)`."""
    return first_line(h.capture(argv, stderr=DEVNULL)[1])


def has_nvidia_gpu(h):
    """`lspci | grep -i nvidia` under pipefail; false on a CPU-only dry-run box."""
    rc, out = h.capture(["lspci"], stderr=DEVNULL)
    return rc == 0 and "nvidia" in out.lower()


def pci_bus_id(lspci_d):
    """Xorg BusID (PCI:bus:dev:fn, decimal) of the first NVIDIA display device in `lspci -D`
    output (DDDD:BB:DD.F, hex); "" when there is none."""
    slot = ""
    for line in lspci_d.split("\n"):
        if "nvidia" in line.lower() and re.search("VGA|3D|Display", line, re.IGNORECASE):
            slot = (line.split() or [""])[0]
            break
    if not slot:
        return ""
    slot = slot.split(":", 1)[-1]
    bus = slot.split(":", 1)[0]
    dev = slot.split(":", 1)[-1].split(".", 1)[0]
    fn = slot.split(".", 1)[-1]
    try:
        return "PCI:%d:%d:%d" % (int(bus, 16), int(dev, 16), int(fn, 16))
    except ValueError:
        return ""


def xorg_conf(bus_id):
    """Headless Xorg config: one 1920x1080 mode with an equal Virtual size (a larger Virtual
    offsets the viewport and breaks RustDesk's absolute pointer), forcing a virtual DFP."""
    return f"""# racecast headless X (generated by provision.py). Single 1920x1080, no monitor.
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "Device0"
    Driver     "nvidia"
    BusID      "{bus_id}"
    Option     "AllowEmptyInitialConfiguration" "true"
    Option     "ConnectedMonitor" "DFP-0"
    Option     "ModeValidation" "AllowNonEdidModes"
EndSection

Section "Monitor"
    Identifier "Monitor0"
    HorizSync  28.0 - 80.0
    VertRefresh 48.0 - 75.0
EndSection

Section "Screen"
    Identifier "Screen0"
    Device     "Device0"
    Monitor    "Monitor0"
    DefaultDepth 24
    SubSection "Display"
        Depth  24
        Modes  "1920x1080"
        Virtual 1920 1080
    EndSubSection
EndSection
"""


def autologin_conf(user):
    return f"[Seat:*]\nautologin-user={user}\nautologin-user-timeout=0\nuser-session=xfce\n"


def app_desktop(name, exec_path):
    return (f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_path}\n"
            "X-GNOME-Autostart-enabled=true\n")


def locker_desktop(locker):
    return (f"[Desktop Entry]\nType=Application\nName={locker} (disabled by racecast: a "
            "passwordless-autologin box must never lock to the greeter)\nExec=/bin/true\nHidden=true\n"
            "X-GNOME-Autostart-enabled=false\n")


def rustdesk_helper(user):
    return RUSTDESK_HELPER.replace("__USER__", user, 1)


def sudoers_line(user):
    return f"{user} ALL=(ALL) NOPASSWD:ALL\n"


def cuda_arch(machine):
    return "sbsa" if machine in ("aarch64", "arm64") else "x86_64"


def release_url(tag, machine):
    asset = "racecast-linux-arm64.tar.gz" if machine in ("aarch64", "arm64") else "racecast-linux.tar.gz"
    if tag == "latest":
        return f"https://github.com/{RACECAST_REPO}/releases/latest/download/{asset}"
    return f"https://github.com/{RACECAST_REPO}/releases/download/{tag}/{asset}"


def new_password():
    """16 characters from A-Za-z0-9, the alphabet of `tr -dc 'A-Za-z0-9' </dev/urandom`."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


def is_snap(h, path):
    """`readlink -f path | grep /snap/` under pipefail."""
    return "/snap/" in h.realpath(path)


def write_gui_autostart(h, user_home, user_name):
    """OBS + Discord autostart through prompt-free launch wrappers, with the screen lockers off,
    so an unattended autologin box survives a hard stop/reboot without interactive dialogs."""
    grp = group_of(h, user_name)
    autostart, bindir = user_home + "/.config/autostart", user_home + "/.local/bin"
    strict(h.run(["install", "-d", "-o", user_name, "-g", grp, autostart, bindir]))
    h.write(bindir + "/racecast-obs-launch", OBS_LAUNCH)
    h.write(bindir + "/racecast-discord-launch", DISCORD_LAUNCH)
    strict(h.run(["chmod", "0755", bindir + "/racecast-obs-launch", bindir + "/racecast-discord-launch"]))
    h.write(autostart + "/racecast-obs.desktop", app_desktop("OBS Studio (racecast)", bindir + "/racecast-obs-launch"))
    h.write(autostart + "/racecast-discord.desktop",
            app_desktop("Discord (racecast)", bindir + "/racecast-discord-launch"))
    for locker in ("light-locker", "xscreensaver"):
        h.write(f"{autostart}/{locker}.desktop", locker_desktop(locker))
    strict(h.run(["chown", "-R", f"{user_name}:{grp}", autostart, bindir]))


def write_rustdesk_setup_helper(h, user_name):
    """First-boot root helper: RustDesk's password/ID/direct-IP need the graphical session, so a
    oneshot after graphical.target applies them and writes ~USER/rustdesk-access.txt."""
    h.write("/usr/local/sbin/racecast-rustdesk-setup", rustdesk_helper(user_name))
    strict(h.run(["chmod", "0755", "/usr/local/sbin/racecast-rustdesk-setup"]))


def ensure_user(h, user):
    if h.run(["getent", "passwd", user], stdout=DEVNULL):
        strict(h.run(["useradd", "-m", "-s", "/bin/bash", user]))
        ok(f"created event user {user} (home {home_of(h, user, check=False)})")
    # render+video for the GPU and NVENC, audio for PipeWire capture; absent groups are skipped.
    for g in ("video", "render", "audio", "plugdev"):
        if h.run(["getent", "group", g], stdout=DEVNULL) == 0:
            h.run(["usermod", "-aG", g, user])


def step_apt(h):
    log("1/10  APT base + upgrade")
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    strict(h.run(["apt-get", "update"]))
    strict(h.run(["apt-get", "-y", "upgrade"]))
    # python3-venv/pip: install-tools' streamlink venv; mesa-utils/x11-utils: glxinfo and xdpyinfo checks.
    strict(h.run(["apt-get", "install", "-y", "curl", "python3", "python3-venv", "python3-pip",
                  "ca-certificates", "wget", "gnupg", "pciutils", "mesa-utils", "x11-utils"]))
    ok("base packages present")


def apt_key_has_cuda(h):
    """`apt-key list | grep -qi cuda` under pipefail."""
    rc, keys = h.capture(["apt-key", "list"], stderr=DEVNULL)
    return rc == 0 and "cuda" in keys.lower()


def dkms_built(h):
    """`dkms status | grep -qi 'nvidia.*installed'` under pipefail."""
    rc, out = h.capture(["dkms", "status"], stderr=DEVNULL)
    return rc == 0 and re.search("nvidia.*installed", out, re.IGNORECASE) is not None


def gpu_name(h):
    return first_line(h.capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])[1])


def step_nvidia(h):
    log("2/10  NVIDIA driver (nvidia-open via CUDA apt repo) + headless X config")
    # nvidia-open (open DKMS modules from the CUDA repo) builds on the 24.04 HWE kernel;
    # Google's install_gpu_driver.py pins a driver whose modules do not.
    if not has_nvidia_gpu(h):
        warn("no NVIDIA GPU on this host. Skipping driver + xorg (CPU dry-run mode)")
    elif h.run(["nvidia-smi"], **QUIET) == 0:
        ok(f"driver already loaded ({gpu_name(h)})")
    else:
        arch = cuda_arch(platform.machine())
        if (not h.isfile(f"/etc/apt/sources.list.d/cuda-ubuntu2404-{arch}.list")
                and not apt_key_has_cuda(h)):
            strict(h.run(["curl", "-fsSL", "https://developer.download.nvidia.com/compute/cuda/repos/"
                          f"ubuntu2404/{arch}/cuda-keyring_1.1-1_all.deb", "-o", "/tmp/cuda-keyring.deb"]))
            strict(h.run(["dpkg", "-i", "/tmp/cuda-keyring.deb"]))
            strict(h.run(["apt-get", "update"]))
        if h.run(["apt-get", "install", "-y", "nvidia-open"]):
            warn("nvidia-open apt install returned non-zero (see output)")
        # The package's ldconfig trigger can be deferred; refresh the cache so this run's NVENC checks see the libs.
        strict(h.run(["ldconfig"]))
        if h.run(["nvidia-smi"], **QUIET) == 0:
            ok(f"driver loaded ({gpu_name(h)})")
        elif dkms_built(h) or h.run(["modinfo", "nvidia"], **QUIET) == 0:
            warn("driver module BUILT but not loaded. Reboot, then re-run provision.py")
        else:
            warn("NVIDIA kernel module BUILD FAILED (not just a reboot). Check: dkms status ;")
            warn("  dmesg | grep -i nvidia ; ensure linux-headers-$(uname -r) is installed.")
    # nvidia-open ships no nvidia-xconfig, so the headless xorg.conf is written by hand; BusID differs per instance.
    if has_nvidia_gpu(h):
        bus_id = pci_bus_id(h.capture(["lspci", "-D"], stderr=DEVNULL)[1])
        current = h.read_bytes("/etc/X11/xorg.conf")
        if not bus_id:
            warn("could not resolve the NVIDIA PCI BusID from lspci. Skipping xorg.conf")
        elif h.isfile("/etc/X11/xorg.conf") and not (current and b"racecast headless X" in current):
            warn("/etc/X11/xorg.conf exists and is not ours. Leaving it untouched")
        else:
            h.write("/etc/X11/xorg.conf", xorg_conf(bus_id))
            ok(f"xorg.conf written (headless 1080p on {bus_id})")


def step_desktop(h, user):
    log("3/10  xfce desktop + autologin (OBS needs a real X11 session)")
    strict(h.run(["apt-get", "install", "-y", "xfce4", "xfce4-goodies", "lightdm"]))
    # The racecast autologin session on :0 is what RustDesk mirrors and OBS autostarts into.
    strict(h.run(["install", "-d", "-m", "0755", "/etc/lightdm/lightdm.conf.d"]))
    h.write("/etc/lightdm/lightdm.conf.d/50-racecast-autologin.conf", autologin_conf(user))
    ok(f"autologin configured for {user}")
    write_gui_autostart(h, home_of(h, user, check=False), user)
    log("   GUI autostart written (OBS + Discord launch with the session; takes effect next boot)")


def step_firefox(h):
    log("4/10  Firefox (deb from Mozilla APT, not snap, snap confinement breaks cookie export)")
    ff = h.which("firefox")
    if ff and not is_snap(h, ff):
        ok("non-snap Firefox already present")
        return
    strict(h.run(["install", "-d", "-m", "0755", "/etc/apt/keyrings"]))
    strict(h.run(["wget", "-q", "https://packages.mozilla.org/apt/repo-signing-key.gpg",
                  "-O", "/etc/apt/keyrings/packages.mozilla.org.asc"]))
    h.write("/etc/apt/sources.list.d/mozilla.list", MOZILLA_LIST)
    h.write("/etc/apt/preferences.d/mozilla", MOZILLA_PIN)
    strict(h.run(["apt-get", "update"]))
    strict(h.run(["apt-get", "install", "-y", "firefox"]))
    ok("Firefox (deb) installed")


def step_rustdesk(h, user, env):
    log("5/10  RustDesk (remote desktop over the tailnet). Install + auto-configure")
    version = env.get("RUSTDESK_VERSION") or RUSTDESK_DEFAULT_VERSION
    if h.which("rustdesk"):
        ok("RustDesk already present")
    else:
        deb = f"/tmp/rustdesk-{version}.deb"
        strict(h.run(["curl", "-fsSL", f"https://github.com/rustdesk/rustdesk/releases/download/{version}/"
                      f"rustdesk-{version}-x86_64.deb", "-o", deb]))
        strict(h.run(["apt-get", "install", "-y", deb]))
        ok(f"RustDesk {version} installed")
    h.run(["systemctl", "enable", "--now", "rustdesk"], **QUIET)
    # Password/ID/direct-IP need the graphical session, so a first-boot oneshot applies them.
    strict(h.run(["install", "-d", "-m", "0700", "/etc/racecast"]))
    if not h.size("/etc/racecast/rustdesk-password"):
        h.write("/etc/racecast/rustdesk-password", env.get("RUSTDESK_PASSWORD") or new_password())
        strict(h.run(["chmod", "0600", "/etc/racecast/rustdesk-password"]))
    write_rustdesk_setup_helper(h, user)
    h.write("/etc/systemd/system/racecast-rustdesk-setup.service", RUSTDESK_UNIT)
    h.run(["systemctl", "enable", "racecast-rustdesk-setup.service"], **QUIET)
    ok(f"RustDesk auto-config armed: on first boot the ID + password land in ~{user}/rustdesk-access.txt")


def step_sudo(h, user):
    log("6/10  passwordless sudo")
    f = "/etc/sudoers.d/90-racecast"
    h.write(f, sudoers_line(user))
    strict(h.run(["chmod", "0440", f]))
    if h.run(["visudo", "-cf", f], **QUIET) == 0:
        ok(f"passwordless sudo for {user}")
    else:
        h.run(["rm", "-f", f])
        warn("sudoers validation failed: drop-in removed")


def step_racecast(h, user, env):
    log("7/10  racecast binary (installed straight into the racecast user's home, user-owned tree)")
    # The frozen binary keeps profiles/ + runtime/ beside itself, so the user's HOME is the install root.
    if h.which("racecast"):
        ok(f"racecast already on PATH ({version_of(h, ['sudo', '-u', user, 'racecast', '--version'])})")
    else:
        url = release_url(env.get("RACECAST_TAG") or "latest", platform.machine())
        user_home = home_of(h, user)
        user_group = group_of(h, user)
        strict(h.run(["curl", "-fsSL", url, "-o", "/tmp/racecast.tar.gz"]))
        strict(h.run(["tar", "-xzf", "/tmp/racecast.tar.gz", "-C", user_home]))
        strict(h.run(["chown", "-R", f"{user}:{user_group}", user_home]))
        strict(h.run(["ln", "-sf", user_home + "/racecast", "/usr/local/bin/racecast"]))
        ok(f"racecast installed in {user_home} ({version_of(h, ['sudo', '-u', user, 'racecast', '--version'])})")

    # install-apps writes the PipeWire plugin as the user; own the leaf tree before a root sub-step creates it.
    uh = home_of(h, user)
    grp = group_of(h, user)
    strict(h.run(["install", "-d", "-o", user, "-g", grp, "-m", "0755",
                  uh + "/.config", uh + "/.config/obs-studio", uh + "/.config/obs-studio/plugins"]))


def step_install_tools(h, user):
    log(f"8/10  racecast install-tools (yt-dlp / streamlink / ffmpeg / deno). As {user}")
    if h.which("racecast"):
        if h.run(["sudo", "-u", user, "-H", "racecast", "install-tools"]):
            warn("install-tools reported issues: see the verification block below")
        ok("install-tools step done")
    else:
        warn("racecast not installed / no login user. Skipping install-tools")


def step_install_apps(h, user):
    log("9/10  racecast install-apps (OBS + Browser Source + PipeWire audio plugin, Tailscale, Companion, "
        f"Discord). As {user}")
    if h.which("racecast"):
        if h.run(["sudo", "-u", user, "-H", "racecast", "install-apps", "--yes"]):
            warn("install-apps reported issues: see the verification block below")
        ok("install-apps step done")
        # Repair root-owned droppings a sudo sub-step may leave in the user's tree; best-effort.
        uh = home_of(h, user)
        h.run(["chown", "-R", f"{user}:{group_of(h, user, check=False)}", uh + "/.config"], stderr=DEVNULL)
    else:
        warn("racecast not installed / no login user. Skipping install-apps")


def tailnet_ip(h):
    return first_line(h.capture(["tailscale", "ip", "-4"], stderr=DEVNULL)[1])


def step_tailscale(h, user, env):
    log("10/10  Tailscale join (REQUIRED, the tailnet is the box's trust boundary)")
    if h.run(["tailscale", "status"], **QUIET) == 0:
        ok(f"already joined the tailnet ({tailnet_ip(h)})")
    elif env.get("TS_AUTHKEY"):
        strict(h.run(["tailscale", "up", "--ssh", "--authkey", env["TS_AUTHKEY"], "--hostname", "racecast-box"]))
        ok(f"joined the tailnet unattended ({tailnet_ip(h)})")
    elif h.isatty():
        log("   ACTION REQUIRED: approve this box into your tailnet.")
        log("   'tailscale up' prints a https://login.tailscale.com/… URL below; open it in your")
        log("   LAPTOP browser and approve. Provisioning WAITS here until you finish.")
        if h.run(["tailscale", "up", "--ssh", "--hostname", "racecast-box"]) == 0:
            ok(f"joined the tailnet ({tailnet_ip(h)})")
        else:
            warn("tailscale up did not complete. Re-run: sudo tailscale up --ssh --hostname racecast-box")
    else:
        warn("REQUIRED join not done: no TS_AUTHKEY and no interactive terminal (detached run).")
        warn("The box is NOT reachable over the tailnet yet. Run this once and approve the URL")
        warn("in your LAPTOP browser:")
        print("      sudo tailscale up --ssh --hostname racecast-box")

    # `racecast funnel on` writes the serve config as the user, which needs the tailscale operator role.
    if h.which("tailscale"):
        if h.run(["tailscale", "set", f"--operator={user}"], **QUIET) == 0:
            ok(f"tailscale operator set to {user} ('racecast funnel on' works without sudo)")
        else:
            warn(f"could not set tailscale operator. 'racecast funnel on' will need it: "
                 f"sudo tailscale set --operator={user}")


def step_copy_prepare_event(h, user, here):
    log(f"copying prepare-event.py into ~{user} (per-event prep helper)")
    # A startup-script run has no sibling file on disk, so a missing copy only warns.
    prep_src = here + "/prepare-event.py"
    if h.isfile(prep_src):
        if h.run(["install", "-m", "0755", "-o", user, "-g", group_of(h, user, check=False), prep_src,
                  f"/home/{user}/prepare-event.py"]) == 0:
            ok(f"prepare-event.py -> /home/{user}/prepare-event.py")
        else:
            warn("could not copy prepare-event.py (continuing)")
    else:
        warn("prepare-event.py not beside provision.py (startup-script mode?). Scp it up manually later")


def verify(h, user):
    """The green/red block: return 1 when a required component is missing, else 0."""
    log("verification: every REQUIRED component below must be PRESENT. There are no optional")
    log("steps: a red PRESENCE line means the box is NOT fully equipped. provision is idempotent")
    log("Fix the cause and re-run; nothing is skipped on a real (GPU) event box.")
    rc = 0
    h.run(["ldconfig"], stderr=DEVNULL)
    pw_home = home_of(h, user)
    mbin = pw_home + "/runtime/bin"

    def check(present, good, bad):
        nonlocal rc
        if present:
            ok(good)
        else:
            warn(bad)
            rc = 1

    for tool, note in (("yt-dlp", "managed"), ("streamlink", "managed venv"), ("deno", "managed")):
        path = f"{mbin}/{tool}"
        present = h.isexec(path)
        check(present, f"{tool}: {version_of(h, [path, '--version']) if present else ''} ({note})",
              f"{tool}: MISSING. Re-run install-tools")
    check(h.which("ffmpeg"), "ffmpeg: present", "ffmpeg: MISSING. Re-run install-tools")

    has_rc = h.which("racecast")
    check(has_rc, f"racecast: {version_of(h, ['racecast', '--version']) if has_rc else ''}", "racecast: MISSING")
    ff = h.which("firefox")
    check(ff and not is_snap(h, ff), "firefox: deb build",
          "firefox: MISSING or snap (cookie export needs the deb build)")
    check(h.which("rustdesk"), "rustdesk: present", "rustdesk: MISSING. Re-run step 5")
    check(h.run(["dpkg", "-s", "lightdm"], **QUIET) == 0, "lightdm/xfce desktop: installed",
          "lightdm: MISSING. Re-run step 3")

    check(h.which("obs"), "obs: installed", "obs: MISSING. Re-run install-apps")
    browser = OBS_BROWSER_SYSTEM + (pw_home + "/.config/obs-studio/plugins/obs-browser/bin/64bit/obs-browser.so",)
    check(any(h.isfile(p) for p in browser), "OBS Browser Source plugin: present (HUD/timer overlays)",
          "OBS Browser Source plugin: MISSING. The relay HUD/timer overlays need it")
    check(h.isfile(pw_home + "/.config/obs-studio/plugins/linux-pipewire-audio/bin/64bit/linux-pipewire-audio.so"),
          "OBS PipeWire audio plugin: present (Discord audio source)",
          "OBS PipeWire audio plugin: MISSING. Re-run install-apps")
    check(h.exists("/opt/companion") or h.exists("/etc/systemd/system/companion.service"),
          "companion: installed (companion-pi service)", "companion: MISSING. Re-run install-apps")
    check(h.exists("/usr/bin/discord") or h.exists("/usr/share/discord"), "discord: installed",
          "discord: MISSING. Re-run install-apps")
    check(h.which("tailscale"), "tailscale: installed", "tailscale: MISSING. Re-run install-apps")

    # Only the CPU dry-run legitimately lacks the GPU stack; on a GPU box it is a hard failure.
    if has_nvidia_gpu(h):
        smi = h.run(["nvidia-smi"], **QUIET) == 0
        driver = version_of(h, ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]) if smi else ""
        check(smi, f"nvidia driver: {driver}",
              "nvidia driver: MISSING. Nvidia-smi fails (reboot + re-run, or check the DKMS build)")
        lrc, cache = h.capture(["ldconfig", "-p"])
        check(lrc == 0 and "nvidia-encode" in cache, "libnvidia-encode: present (NVENC)",
              "libnvidia-encode: MISSING. NVENC needs it")
        nvenc = False
        if h.which("ffmpeg"):
            frc, encoders = h.capture(["ffmpeg", "-hide_banner", "-encoders"], stderr=DEVNULL)
            nvenc = frc == 0 and "nvenc" in encoders
        check(nvenc, "ffmpeg NVENC encoders: present", "ffmpeg NVENC encoders: MISSING")
    else:
        warn("no NVIDIA GPU on this host. GPU stack (driver/NVENC/X-on-GPU) NOT verified. This is")
        warn("the CPU dry-run only; a real event box MUST run on a GPU box (has_nvidia_gpu=true).")

    # Post-reboot / post-join state: advisory, it does not fail the equipped-ness gate.
    if h.run(["tailscale", "status"], **QUIET) == 0:
        ok(f"tailnet: joined ({tailnet_ip(h)})")
    else:
        warn("tailnet: not joined yet. Complete the REQUIRED browser-auth join (step 10)")
    if h.run(["pgrep", "-x", "Xorg"], **QUIET) == 0:
        ok("X server: running")
    else:
        warn("X server: not running yet. Reboot to start the racecast autologin session (RustDesk needs it)")
    if has_nvidia_gpu(h) and h.run(["pgrep", "-x", "Xorg"], **QUIET) == 0:
        grc, glx = h.capture(["glxinfo"], stderr=DEVNULL, env=dict(os.environ, DISPLAY=":0"))
        if grc == 0 and "nvidia" in glx.lower():
            ok("OpenGL renderer: NVIDIA (X is on the GPU)")
        else:
            warn("OpenGL renderer not NVIDIA yet. Re-check after reboot: DISPLAY=:0 glxinfo -B")

    uh = home_of(h, user)
    if (h.isexec(uh + "/.local/bin/racecast-obs-launch") and h.isexec(uh + "/.local/bin/racecast-discord-launch")
            and h.exists(uh + "/.config/autostart/light-locker.desktop")):
        ok("unattended-desktop hardening: OBS/Discord launch wrappers + locker suppression in place")
    else:
        warn("unattended-desktop hardening incomplete: re-run provision (step 3 write_gui_autostart)")
    return rc


def finish(h, user, rc, env):
    print()
    if rc == 0:
        log("provision.py complete. Box FULLY EQUIPPED (every required component present).")
        print(f"      Event stack is owned by '{user}' (its home: {home_of(h, user, check=False)}).")
        print(f"      Log in as that user directly. 'gcloud compute ssh {user}@racecast-box'.")
        print("      Then run racecast plainly: 'racecast preflight', 'racecast event start', …")
        print("      If warned above, finish the tailnet join. The reboot below brings up the desktop")
        print(f"      + auto-configures RustDesk: afterwards 'cat ~{user}/rustdesk-access.txt' for the")
        print("      ID + password. Then: per-league onboarding (profile + cookies + setup + OBS import).")
    else:
        warn("provision.py finished with MISSING components (red PRESENCE lines above). The box is")
        warn("NOT fully equipped. Fix the cause and re-run provision; it is idempotent and will")
        warn("install only what is still missing. Do NOT proceed to an event with a red line.")

    # The reboot brings up the autologin desktop and runs the first-boot RustDesk config.
    if (env.get("PROVISION_REBOOT") or "1") != "0":
        log("Rebooting in 10s to bring up the desktop session + finish RustDesk setup.")
        log("  Opt out with PROVISION_REBOOT=0. Press Ctrl-C now to cancel.")
        h.sleep(10)
        strict(h.run(["reboot"]))
    else:
        log("PROVISION_REBOOT=0: skipping the reboot. Reboot manually to bring up the desktop")
        log("session (RustDesk/OBS need X on :0) and run the first-boot RustDesk config.")
    sys.exit(rc)


def main(env=None, h=None, here=None):
    env = os.environ if env is None else env
    h = h or Host()
    here = here or os.path.dirname(os.path.abspath(__file__))
    if h.capture(["id", "-u"])[1] != "0":
        print("provision.py must run as root (use: sudo ./provision.py)", file=sys.stderr)
        sys.exit(1)
    # One operational account owns the autologin session, OBS, sudoers and the install tree.
    user = env.get("RACECAST_USER") or "racecast"
    ensure_user(h, user)

    step_apt(h)
    step_nvidia(h)
    step_desktop(h, user)
    step_firefox(h)
    step_rustdesk(h, user, env)
    step_sudo(h, user)
    step_racecast(h, user, env)
    step_install_tools(h, user)
    step_install_apps(h, user)
    step_tailscale(h, user, env)
    step_copy_prepare_event(h, user, here)
    finish(h, user, verify(h, user), env)


def _harden_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass  # not a reconfigurable text stream; keep it as it is


if __name__ == "__main__":
    _harden_stdio()
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
