#!/usr/bin/env python3
"""provision-iptest.py: MINIMAL test-box provisioning, derived from provision.py, for the
#505 YouTube-egress-IP investigation. It stands up JUST enough to answer one question
faithfully: does THIS box's egress IP get YouTube-bot-blocked the way the AWS event box
does?  It reuses the SAME layers a real box uses: a dedicated `racecast` user, the
racecast binary, and `racecast install-tools` (yt-dlp / streamlink / ffmpeg / deno). Plus
it copies the REAL YT cookies, then resolves the test URL(s) with the exact relay command
(`ytdlp_resolve_cmd`: yt-dlp -g -f b[height<=1080]/b --cookies ... -- URL).

EVERYTHING not needed to resolve/pull a feed is STRIPPED vs provision.py: no NVIDIA/GPU,
no xfce desktop, no Firefox, no RustDesk, no OBS/install-apps, no Tailscale join, no
prepare-event. So it runs on a cheap CPU box (t3.small) in seconds, not a GPU box.

Ubuntu 24.04, run as root:  sudo python3 provision-iptest.py
Self-contained (stdlib only, no repo imports): the iptest runners copy it onto the box.

Env:
  RACECAST_TAG    racecast release to install (default: latest). Use `preview-main`
                  only if a stable lacks the streamlink-venv fix and you also test PULLS.
  RACECAST_USER   event user to create/target (default: racecast, same as provision.py).
  IPTEST_COOKIES  path on THIS box to the yt-cookies.txt to copy in (default /tmp/yt-cookies.txt).
                  The orchestrator scp's the real box cookies there BEFORE running this.
  IPTEST_URLS     space-separated YouTube URLs to resolve (default: one public live URL).
                  Pass the real `youtube.com/live/<id>` commentator/VOD URLs to be exact.
  IPTEST_PULL     1 = after a successful resolve, do a 10 s streamlink byte-pull too
                  (proves the full feed path, not just the resolve). Default 0.
  IPTEST_HARNESS  1 = also deploy the #505 concurrency harness (branch source tree, so
                  tools/multifeed-429-probe.py + src/ are present) as the racecast user,
                  ready for tools/cloud/iptest-505-run.py. Default 0.
  IPTEST_HARNESS_REF   git branch/tag of the source to fetch for the harness
                       (default: feat/505-multifeed-429-probe).
"""
import os, platform, shutil, subprocess, sys

RACECAST_REPO = "jegr78/gt-racing-broadcast"
YTDLP_FMT = "b[height<=1080]/b"   # mirrors racecast-feeds.py YTDLP_FORMAT exactly
DEVNULL = subprocess.DEVNULL


class Host:
    """Process and filesystem access for the real run; the tests substitute a recorder."""

    def which(self, name):
        return shutil.which(name)

    def home(self, user):
        """The user's home directory, or None for an unknown user."""
        path = os.path.expanduser("~" + user)
        return None if path.startswith("~") else path

    def isfile(self, path):
        return os.path.isfile(path)

    def isexec(self, path):
        return os.access(path, os.X_OK)

    def read_bytes(self, path):
        with open(path, "rb") as f:
            return f.read()

    def run(self, argv, stdout=None, stderr=None):
        """Run argv with inherited stdio unless redirected; return its exit status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            return subprocess.run(argv, stdout=stdout, stderr=stderr).returncode
        except FileNotFoundError:
            if stderr is None:
                print(f"{argv[0]}: command not found", file=sys.stderr)
            return 127
        except OSError as e:
            if stderr is None:
                print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            return 126

    def capture(self, argv, stderr=None):
        """Run argv; return (exit status, stdout without trailing newlines)."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=stderr,
                               text=True, errors="replace")
        except FileNotFoundError:
            return 127, ""
        except OSError as e:
            if stderr is None:
                print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            return 126, ""
        return p.returncode, p.stdout.rstrip("\n")

    def count_bytes(self, argv):
        """Run argv; return (exit status, number of bytes it wrote to stdout)."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=DEVNULL)
        except FileNotFoundError:
            return 127, 0
        except OSError:
            return 126, 0
        return p.returncode, len(p.stdout)


def log(msg):
    print(f"\n\033[1;34m==>\033[0m {msg}")


def ok(msg):
    print(f"  \033[1;32mOK\033[0m  {msg}")


def warn(msg):
    print(f"  \033[1;33m!!\033[0m  {msg}")


def strict(rc):
    """End the script with rc when the command failed."""
    if rc:
        sys.exit(rc)


def first_line(text):
    return text.split("\n", 1)[0]


def checked(h, argv, stderr=None):
    """The output of argv; a failed command ends the script."""
    rc, out = h.capture(argv, stderr=stderr)
    strict(rc)
    return out


def release_url(tag, machine):
    asset = "racecast-linux-arm64.tar.gz" if machine in ("aarch64", "arm64") else "racecast-linux.tar.gz"
    if tag == "latest":
        return f"https://github.com/{RACECAST_REPO}/releases/latest/download/{asset}"
    return f"https://github.com/{RACECAST_REPO}/releases/download/{tag}/{asset}"


def version_of(h, argv):
    return first_line(h.capture(argv, stderr=DEVNULL)[1])


def resolve_argv(user, rtbin, path, cookie_arg, url):
    return (["sudo", "-u", user, "-H", "env", f"PATH={rtbin}:{path}", rtbin + "/yt-dlp",
             "-g", "-f", YTDLP_FMT, "--no-warnings", "--no-playlist"] + cookie_arg + ["--", url])


def pull_argv(user, rtbin, path, url):
    return ["sudo", "-u", user, "-H", "env", f"PATH={rtbin}:{path}", "timeout", "10",
            rtbin + "/streamlink", "--stdout", "--http-header", "User-Agent=Mozilla/5.0",
            "--", url, "best"]


def verdict(out):
    if out.startswith("http"):
        return f"  RESOLVED  -> {out[:80]}…"
    if "not a bot" in out:
        return "  BOT-CHECK -> IP is YouTube-bot-blocked"
    return f"  OTHER     -> {out[:110]}"


def harness_script(ref):
    """The shell text the harness deploy runs as the event user."""
    return ("\n        cd ~ && curl -fsSL -o iro505.tgz "
            f"'https://codeload.github.com/{RACECAST_REPO}/tar.gz/refs/heads/{ref}' "
            "        && tar xzf iro505.tgz "
            '        && ln -sfn "$(tar tzf iro505.tgz | head -1 | cut -d/ -f1)" iro505 '
            "        && test -f iro505/tools/multifeed-429-probe.py")


def main(env=None, h=None):
    env = os.environ if env is None else env
    h = h or Host()
    user = env.get("RACECAST_USER") or "racecast"
    cookies_src = env.get("IPTEST_COOKIES") or "/tmp/yt-cookies.txt"
    urls = env.get("IPTEST_URLS") or "https://www.youtube.com/@SkyNews/live"

    if h.capture(["id", "-u"])[1] != "0":
        print("provision-iptest.py must run as root (sudo)", file=sys.stderr)
        sys.exit(1)

    if h.run(["id", "-u", user], stdout=DEVNULL, stderr=DEVNULL):
        strict(h.run(["useradd", "-m", "-s", "/bin/bash", user]))
        ok(f"created event user {user} ({h.home(user) or ''})")
    user_home = h.home(user)
    if not user_home:
        print(f"provision-iptest.py: no home directory for user {user}", file=sys.stderr)
        sys.exit(1)
    user_group = checked(h, ["id", "-gn", user])

    # Log in DIRECTLY as racecast with the key the box was launched with, copied from the invoking login user.
    ssh_src_user = env.get("SUDO_USER") or "ubuntu"
    src_home = h.home(ssh_src_user)
    src_ak = f"{src_home}/.ssh/authorized_keys"
    if not src_home:
        warn(f"no user {ssh_src_user} on this box: racecast SSH access not set up (set SUDO_USER)")
    elif h.isfile(src_ak):
        strict(h.run(["install", "-d", "-o", user, "-g", user_group, "-m", "0700", user_home + "/.ssh"]))
        strict(h.run(["install", "-m", "0600", "-o", user, "-g", user_group, src_ak,
                      user_home + "/.ssh/authorized_keys"]))
        ok(f"racecast SSH access enabled (key from {ssh_src_user}). 'ssh racecast@<ip>' works directly")
    else:
        warn(f"no authorized_keys at {src_ak}. Reach racecast via 'ssh {ssh_src_user}@<ip> sudo -u racecast …'")

    log("1/4  APT base")
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    strict(h.run(["apt-get", "update", "-qq"]))
    strict(h.run(["apt-get", "install", "-y", "curl", "python3", "python3-venv", "python3-pip",
                  "ca-certificates", "wget", "gnupg"], stdout=DEVNULL))
    ok("base packages present")

    log("2/4  racecast binary")
    if h.which("racecast"):
        ok(f"racecast already on PATH ({version_of(h, ['sudo', '-u', user, 'racecast', '--version'])})")
    else:
        url = release_url(env.get("RACECAST_TAG") or "latest", platform.machine())
        strict(h.run(["curl", "-fsSL", url, "-o", "/tmp/racecast.tar.gz"]))
        strict(h.run(["tar", "-xzf", "/tmp/racecast.tar.gz", "-C", user_home]))
        strict(h.run(["chown", "-R", f"{user}:{user_group}", user_home]))
        strict(h.run(["ln", "-sf", user_home + "/racecast", "/usr/local/bin/racecast"]))
        ok(f"racecast installed ({version_of(h, ['sudo', '-u', user, 'racecast', '--version'])})")

    log(f"3/4  racecast install-tools (yt-dlp / streamlink / ffmpeg / deno). As {user}")
    if h.run(["sudo", "-u", user, "-H", "racecast", "install-tools"]):
        warn("install-tools reported issues: see the resolve step below")
    rtbin = user_home + "/runtime/bin"
    for t in ("yt-dlp", "streamlink", "deno"):
        if h.isexec(f"{rtbin}/{t}"):
            ok(f"{t} present ({version_of(h, [f'{rtbin}/{t}', '--version'])})")
        else:
            warn(f"{t} MISSING at {rtbin}: feed path will fail")
    # Only the install-tools venv streamlink (>=8.2.0) has --http-cookies-file; apt's is too old.
    rc, helptext = h.capture([rtbin + "/streamlink", "--help"], stderr=DEVNULL)
    if rc == 0 and "--http-cookies-file" in helptext:
        ok("streamlink supports --http-cookies-file (venv build OK, the apt-too-old note above is expected)")
    else:
        warn("streamlink lacks --http-cookies-file: YouTube pulls will 403 (venv build failed; re-run install-tools)")

    log("copy YT cookies")
    cookies_dst = user_home + "/runtime/yt-cookies.txt"
    if h.isfile(cookies_src):
        strict(h.run(["install", "-d", "-o", user, "-g", user_group, "-m", "0755", user_home + "/runtime"]))
        strict(h.run(["install", "-m", "0600", "-o", user, "-g", user_group, cookies_src, cookies_dst]))
        lines = h.read_bytes(cookies_src).count(b"\n")
        ok(f"cookies copied -> {cookies_dst} ({lines} lines)")
    else:
        warn(f"no cookies at {cookies_src}. Resolve will run WITHOUT --cookies (still a valid IP test,")
        warn("  but not identical to the relay; stage the box's yt-cookies.txt there to be exact)")

    log("4/4  resolve test from this box's egress IP")
    cookie_arg = ["--cookies", cookies_dst] if h.isfile(cookies_dst) else []
    rc, ip = h.capture(["curl", "-s", "https://api.ipify.org"])
    print(f"  egress IP (as the internet sees it): {ip if rc == 0 else f'<unknown, curl exit {rc}>'}")
    path = os.environ.get("PATH", "")
    for u in urls.split():
        print(f"  --- {u} ---")
        # A failed resolve is a verdict (the bot check exits non-zero), so its exit status is not checked.
        _, out = h.capture(resolve_argv(user, rtbin, path, cookie_arg, u), stderr=subprocess.STDOUT)
        out = first_line(out)
        print(verdict(out))
        if env.get("IPTEST_PULL", "0") == "1" and out[:4] == "http":
            # `timeout 10` always ends a live pull, so the byte count is the result, not the status.
            _, nbytes = h.count_bytes(pull_argv(user, rtbin, path, out))
            print(f"  pull 10s: {nbytes} bytes")
    if env.get("IPTEST_HARNESS", "0") == "1":
        log(f"deploy #505 harness source (as {user})")
        ref = env.get("IPTEST_HARNESS_REF") or "feat/505-multifeed-429-probe"
        if h.run(["sudo", "-u", user, "-H", "bash", "-lc", harness_script(ref)]) == 0:
            ok(f"harness at {user_home}/iro505 (ref {ref}). Src/ + tools/ present")
        else:
            warn(f"harness deploy failed (ref {ref})")

    log("provision-iptest.py complete. Resolve verdict(s) above are the #505 answer for this IP")


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
