#!/usr/bin/env python3
"""prepare-event.py: on-box, per-event racecast preparation for the cloud GPU box.

Runs the recurring event-prep sequence to "ready" (no go-live) and reports which
one-time manual setup is still missing. Companion to tools/cloud/provision.py.
Run as the `racecast` user on the box:  ./prepare-event.py <league> [flags]
Self-contained (stdlib only, no repo imports): it is copied onto the box on its own.
"""
import os, re, shutil, subprocess, sys

USAGE = """Usage: ./prepare-event.py <league> [--no-twitch] [--no-speedtest] [--no-update]

  <league>        racecast profile name for this event (required; must be imported)
  --no-twitch     skip the Twitch cookie/auth refresh (default: run it alongside YouTube)
  --no-speedtest  skip the bandwidth test (default: run it)
  --no-update     skip the racecast binary self-update (default: run it, with preview guard)

Prepares the box to "ready"; it never goes live (no `racecast event start`).
"""

QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


class Host:
    """Process and filesystem access for the real run; the tests substitute a recorder."""

    def which(self, name):
        return shutil.which(name)

    def realpath(self, path):
        return os.path.realpath(path)

    def size(self, path):
        """Size in bytes, or None when the path does not exist (bash -s is size > 0)."""
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def read(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def isatty(self):
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except (ValueError, OSError):
            return False

    def ask(self, prompt):
        """bash read -r -p: the prompt goes to stderr, surrounding blanks are stripped."""
        sys.stderr.write(prompt)
        sys.stderr.flush()
        return sys.stdin.readline().strip(" \t\n")

    def run(self, argv, stdout=None, stderr=None):
        """Run argv with inherited stdio unless redirected; return its exit status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            return subprocess.run(argv, stdout=stdout, stderr=stderr).returncode
        except FileNotFoundError:
            if stderr is None:
                print(f"{argv[0]}: command not found", file=sys.stderr)
            return 127

    def capture(self, argv, stderr=None):
        """bash $(argv): (exit status, stdout without trailing newlines)."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=stderr,
                               text=True, errors="replace")
        except FileNotFoundError:
            return 127, ""
        return p.returncode, p.stdout.rstrip("\n")


class Die(SystemExit):
    pass


class Prep:
    def __init__(self, h, env, argv):
        self.h = h
        self.argv = list(argv)
        self.user = env.get("RACECAST_USER") or "racecast"
        self.league = ""
        self.no_twitch = self.no_speedtest = self.no_update = False
        self.soft_warnings = 0
        self.preflight_rc = 0
        self.root = self.runtime = self.profiles = ""

    def log(self, msg):
        print(f"\033[1;34m[prepare]\033[0m {msg}")

    def warn(self, msg):
        print(f"\033[1;33m[warn]\033[0m {msg}", file=sys.stderr)
        self.soft_warnings += 1

    def die(self, msg):
        print(f"\033[1;31m[error]\033[0m {msg}", file=sys.stderr)
        raise Die(1)

    def usage(self):
        print(USAGE, end="")

    def parse_args(self):
        for a in self.argv:
            if a == "--no-twitch":
                self.no_twitch = True
            elif a == "--no-speedtest":
                self.no_speedtest = True
            elif a == "--no-update":
                self.no_update = True
            elif a in ("-h", "--help"):
                self.usage()
                raise SystemExit(0)
            elif a.startswith("-"):
                self.usage()
                self.die(f"unknown flag: {a}")
            elif not self.league:
                self.league = a
            else:
                self.die(f"unexpected argument: {a}")

    def is_league_imported(self, name):
        rc, out = self.h.capture(["racecast", "profile", "list"], stderr=subprocess.DEVNULL)
        last_fields = [(line.split() or [line])[-1] for line in out.split("\n")]
        return rc == 0 and name in last_fields

    def do_update(self):
        if self.no_update:
            self.log("update: skipped (--no-update)")
            return
        _, cur = self.h.capture(["racecast", "--version"], stderr=subprocess.DEVNULL)
        if "preview" in cur:
            if self.h.isatty():
                print(f"\033[1;33m[prepare]\033[0m Preview build '{cur}' installed (kept for the Linux fixes).")
                ans = self.h.ask("         Update to latest STABLE (loses the preview fixes)? [y/N] ")
                if ans.lower() in ("y", "yes"):
                    if self.h.run(["racecast", "update"]):
                        self.die("racecast update failed")
                else:
                    self.log(f"update: keeping preview build '{cur}'")
            else:
                self.log(f"update: preview build '{cur}' kept (no TTY to confirm). Run interactively, "
                         "or 'racecast update' to move to stable.")
        else:
            self.log(f"update: stable build '{cur}'. Checking for a newer stable")
            if self.h.run(["racecast", "update"]):
                self.die("racecast update failed")

    def resolve_root(self):
        binary = self.h.which("racecast")
        if not binary:
            self.die("racecast not on PATH. Is this the racecast user on a provisioned box?")
        self.root = os.path.dirname(self.h.realpath(binary))
        self.runtime = self.root + "/runtime"
        self.profiles = self.root + "/profiles"

    def try_or_warn(self, argv, msg):
        if self.h.run(argv):
            self.warn(msg)

    def run_prep_sequence(self):
        self.log(f"activating profile '{self.league}'")
        if self.h.run(["racecast", "profile", "use", self.league]):
            self.die(f"racecast profile use '{self.league}' failed")

        self.log("refreshing YouTube cookies")
        self.try_or_warn(["racecast", "cookies", "firefox"],
                         "YouTube cookie refresh failed. Check the box's Firefox is signed in to YouTube")
        if self.no_twitch:
            self.log("Twitch cookies: skipped (--no-twitch)")
        else:
            self.log("refreshing Twitch cookies")
            self.try_or_warn(["racecast", "cookies", "twitch", "firefox"],
                             "Twitch cookie refresh failed. Sign in to Twitch in the box's Firefox, "
                             "or pass --no-twitch")

        self.log("refreshing broadcast graphics")
        self.try_or_warn(["racecast", "graphics"], "graphics refresh failed (OBS shows black for missing files)")
        self.log("refreshing intro/outro media")
        self.try_or_warn(["racecast", "media"], "media refresh failed")
        self.log("refreshing brand logos")
        self.try_or_warn(["racecast", "brands"], "brands refresh failed")

        if self.no_speedtest:
            self.log("speedtest: skipped (--no-speedtest)")
        else:
            self.log("running bandwidth speedtest")
            self.try_or_warn(["racecast", "speedtest"],
                             "speedtest failed (network); preflight bandwidth check may be stale")

        self.log("forcing a clean relay state (stop + free feed ports)")
        self.h.run(["racecast", "relay", "stop"], **QUIET)
        self.h.run(["racecast", "freeport", "--force"], **QUIET)

        self.log("running preflight")
        self.preflight_rc = self.h.run(["racecast", "preflight"])

    def sanity_guard(self):
        if self.h.capture(["id", "-un"])[1] != self.user:
            self.die(f"run as the '{self.user}' user (current: '{self.h.capture(['id', '-un'])[1]}'). "
                     f"Try: sudo -iu {self.user} ./prepare-event.py {' '.join(self.argv)}")
        if not self.h.which("racecast"):
            self.die("racecast not on PATH")
        if not self.league:
            self.usage()
            self.die("missing <league>")
        if not self.is_league_imported(self.league):
            self.die(f"profile '{self.league}' is not imported. Onboard it first "
                     "(see tools/cloud/README.md §4): racecast profile import <bundle>.zip")

    def _ok(self, msg):
        print(f"  \033[1;32mOK\033[0m   {msg}")

    def _bad(self, msg):
        print(f"  \033[1;31mMISS\033[0m {msg}")

    def _note(self, msg):
        print(f"  \033[1;33m--\033[0m   {msg}")

    def tailnet_ips(self):
        """(exit status, lines) of `tailscale ip -4 | grep -E '^100\\.'`."""
        rc, out = self.h.capture(["tailscale", "ip", "-4"], stderr=subprocess.DEVNULL)
        return rc, [line for line in out.split("\n") if re.match(r"100\.", line)]

    def tailnet_joined(self):
        rc, lines = self.tailnet_ips()
        return rc == 0 and bool(lines)

    def league_uses_discord(self):
        text = self.h.read(f"{self.profiles}/{self.league}/profile.env")
        return text is not None and any(line.startswith("DISCORD_CLIENT_ID=") for line in text.split("\n"))

    def discord_token_cached(self):
        if (self.h.size(self.runtime + "/discord-rpc-token.json") or 0) > 0:
            return True
        rc, out = self.h.capture(["find", self.runtime, "-name", "discord-rpc-token.json", "-type", "f"],
                                 stderr=subprocess.DEVNULL)
        return rc == 0 and any(out.split("\n"))

    def readiness_report(self):
        fail = False
        self.log("readiness: one-time setup that neither provision.py nor this script can do:")

        if self.tailnet_joined():
            self._ok(f"tailnet joined ({(self.tailnet_ips()[1] or [''])[0]})")
        else:
            self._bad("tailnet NOT joined. Run:  sudo tailscale up --ssh --hostname racecast-box")
            fail = True

        if (self.h.size(f"{self.runtime}/{self.league}/GT_Racing_Endurance.import.json") or 0) > 0:
            self._ok(f"OBS scene collection localized for '{self.league}'")
        else:
            self._bad("OBS collection not localized. Run 'racecast setup', then import it into OBS "
                      "over RustDesk (once per league)")
            fail = True

        if (self.h.size(self.runtime + "/yt-cookies.txt") or 0) > 0:
            self._ok("YouTube cookies present")
        else:
            self._note("no YouTube cookies yet. Sign in to YouTube in the box's Firefox, then re-run")

        if self.league_uses_discord():
            if self.discord_token_cached():
                self._ok("Discord voice token cached")
            else:
                self._note("league uses Discord but no voice token. Run 'racecast discord join' once over RustDesk")

        if self.preflight_rc == 0:
            self._ok("preflight passed")
        else:
            self._bad(f"preflight reported issues (exit {self.preflight_rc}). See the preflight output above")
            fail = True

        print()
        if not fail:
            self.log("READY: go live via Control Center 'Start event' or:  racecast event start")
            if self.soft_warnings > 0:
                self.warn(f"{self.soft_warnings} soft warning(s) above. Review before going live")
            raise SystemExit(0)
        self.die(f"NOT ready: fix the MISS lines above, then re-run:  ./prepare-event.py {self.league}")

    def main(self):
        self.parse_args()
        self.resolve_root()
        self.sanity_guard()
        self.log(f"profile '{self.league}' found; install root {self.root}")
        self.do_update()
        self.run_prep_sequence()
        self.readiness_report()


def main(argv=None, env=None, h=None):
    Prep(h or Host(), os.environ if env is None else env,
         sys.argv[1:] if argv is None else argv).main()


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
