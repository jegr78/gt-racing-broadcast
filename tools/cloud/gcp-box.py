#!/usr/bin/env python3
"""gcp-box.py. Start / stop / status control wrapper for the GCP GPU box.

Runs on YOUR laptop (not the box). A thin wrapper around the `gcloud` CLI so you
never have to remember the instance name or zone. Cost control: STOP the box
after every event: a running g2-standard-8 (L4) bills by the hour, a stopped
("TERMINATED") one only its boot disk. The tailnet IP is stable across
stop/start (Tailscale), so the box keeps its `100.x` address after a restart.

Usage:
  tools/cloud/gcp-box.py status         # cloud state + type + external IP (default)
  tools/cloud/gcp-box.py start          # start (gcloud waits until RUNNING)
  tools/cloud/gcp-box.py stop           # stop (idle = boot disk only)
  tools/cloud/gcp-box.py ip             # print the current external IP (running only)
  tools/cloud/gcp-box.py ssh [args...]  # gcloud SSH in as racecast

Config (env overrides, never commit machine-specific values):
  GCP_BOX_NAME     instance name         (default racecast-box)
  GCP_BOX_ZONE     zone                  (default europe-west4-b)
  GCP_BOX_PROJECT  project               (default: your active gcloud project)
  GCP_BOX_SSH_USER login user            (default racecast)
"""
import os, shutil, subprocess, sys


class Host:
    """Process access for the real run; the tests substitute a recorder."""

    def which(self, name):
        return shutil.which(name)

    def _argv(self, argv):
        return [shutil.which(argv[0]) or argv[0]] + list(argv[1:])

    def run(self, argv, stdout=None, stderr=None):
        """Run argv with inherited stdio unless redirected; return its exit status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            return subprocess.run(self._argv(argv), stdout=stdout, stderr=stderr).returncode
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
            p = subprocess.run(self._argv(argv), stdout=subprocess.PIPE, stderr=stderr,
                               text=True, errors="replace")
        except FileNotFoundError:
            return 127, ""
        except OSError as e:
            if stderr is None:
                print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            return 126, ""
        return p.returncode, p.stdout.rstrip("\n")

    def exec(self, argv):
        """Replace this process with argv; on Windows run it and exit with its status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            if os.name != "nt":
                os.execvp(argv[0], argv)
            exe = shutil.which(argv[0])
            if not exe:
                raise FileNotFoundError(argv[0])
            sys.exit(subprocess.call([exe] + list(argv[1:])))
        except FileNotFoundError:
            print(f"{argv[0]}: command not found", file=sys.stderr)
            sys.exit(127)
        except OSError as e:
            print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            sys.exit(126)


def die(msg):
    print(f"gcp-box: {msg}", file=sys.stderr)
    sys.exit(1)


def strict(rc):
    """End the script with rc when the command failed."""
    if rc:
        sys.exit(rc)


def config(env):
    project = env.get("GCP_BOX_PROJECT") or ""
    return {
        "name": env.get("GCP_BOX_NAME") or "racecast-box",
        "zone": env.get("GCP_BOX_ZONE") or "europe-west4-b",
        "ssh_user": env.get("GCP_BOX_SSH_USER") or "racecast",
        "project_arg": ["--project", project] if project else [],
    }


def describe_argv(cfg, field):
    return (["gcloud", "compute", "instances", "describe", cfg["name"], "--zone", cfg["zone"]]
            + cfg["project_arg"] + [f"--format=value({field})"])


def describe(h, cfg, field):
    return h.capture(describe_argv(cfg, field), stderr=subprocess.DEVNULL)


def state(h, cfg):
    return describe(h, cfg, "status")


def external_ip(h, cfg):
    """The external IP, or "" when there is none or the lookup fails."""
    rc, ip = describe(h, cfg, "networkInterfaces[0].accessConfigs[0].natIP")
    return "" if rc else ip


def status(h, cfg):
    rc, st = state(h, cfg)
    if rc:
        die("cannot reach GCP (check 'gcloud auth login' / project)")
    if not st:
        die(f"instance {cfg['name']} not found in {cfg['zone']}")
    rc, ty = describe(h, cfg, "machineType.basename()")
    strict(rc)
    print(f"GCP box {cfg['name']} ({ty}, {cfg['zone']})")
    print(f"  state:       {st}")
    if st == "RUNNING":
        ip = external_ip(h, cfg)
        print(f"  external IP: {ip or '<none>'}")
    print(f"  ssh:         gcloud compute ssh {cfg['ssh_user']}@{cfg['name']} --zone {cfg['zone']}"
          "   (tailnet 100.x is stable)")


def start(h, cfg):
    rc, st = state(h, cfg)
    strict(rc)
    if st == "RUNNING":
        print("already running.")
        status(h, cfg)
        return
    print(f"starting {cfg['name']} …")
    strict(h.run(["gcloud", "compute", "instances", "start", cfg["name"], "--zone", cfg["zone"]]
                 + cfg["project_arg"], stdout=subprocess.DEVNULL))
    status(h, cfg)
    print("note: the desktop session + Tailscale take ~1 more minute after RUNNING.")


def stop(h, cfg):
    rc, st = state(h, cfg)
    strict(rc)
    if st == "TERMINATED":
        print("already stopped (TERMINATED).")
        return
    print(f"stopping {cfg['name']} …")
    strict(h.run(["gcloud", "compute", "instances", "stop", cfg["name"], "--zone", cfg["zone"]]
                 + cfg["project_arg"], stdout=subprocess.DEVNULL))
    print("stopped: billing drops to the boot disk only.")


def main(argv=None, env=None, h=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    h = h or Host()
    cfg = config(env)
    if not h.which("gcloud"):
        die("the 'gcloud' CLI is not installed (see cloud SDK)")
    action = argv[0] if argv and argv[0] else "status"
    if action == "status":
        status(h, cfg)
    elif action == "start":
        start(h, cfg)
    elif action == "stop":
        stop(h, cfg)
    elif action == "ip":
        ip = external_ip(h, cfg)
        if not ip:
            die("no external IP (box not running?)")
        print(ip)
    elif action == "ssh":
        h.exec(["gcloud", "compute", "ssh", f"{cfg['ssh_user']}@{cfg['name']}", "--zone", cfg["zone"]]
               + cfg["project_arg"] + argv[1:])
    elif action in ("-h", "--help", "help"):
        print(__doc__, end="")
    else:
        die(f"unknown action '{action}'. Try: status | start | stop | ip | ssh | help")


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
