#!/usr/bin/env python3
"""aws-box.py. Start / stop / status control wrapper for the AWS GPU box.

Runs on YOUR laptop (not the box). A thin wrapper around the `aws` CLI so you
never have to remember the instance id or region. Cost control: STOP the box
after every event: a running g4dn.xlarge bills ~$0.60/h, a stopped one only
its EBS boot disk. The tailnet IP is stable across stop/start (Tailscale), so
`racecast-box-aws` keeps working after a restart.

Usage:
  tools/cloud/aws-box.py status          # cloud state + type + public IP (default)
  tools/cloud/aws-box.py start [--no-wait]   # start; wait until running, print SSH line
  tools/cloud/aws-box.py stop            # stop (idle = boot disk only)
  tools/cloud/aws-box.py ip              # print the current public IP (running only)
  tools/cloud/aws-box.py ssh [args...]   # SSH in as racecast over the tailnet

Config (env overrides, never commit machine-specific values):
  AWS_BOX_ID        instance id           (default i-04d70428c19a484ef)
  AWS_BOX_REGION    region                (default eu-central-1)
  AWS_BOX_SSH_HOST  tailnet host/IP       (default racecast-box-aws)
  AWS_BOX_SSH_USER  login user            (default racecast)
  AWS_BOX_SSH_KEY   private key           (default ~/.ssh/racecast-box.pem)
"""
import os, shutil, subprocess, sys


class Host:
    """Process access for the real run; the tests substitute a recorder."""

    def which(self, name):
        return shutil.which(name)

    def isfile(self, path):
        return os.path.isfile(path)

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
    print(f"aws-box: {msg}", file=sys.stderr)
    sys.exit(1)


def strict(rc):
    """End the script with rc when the command failed."""
    if rc:
        sys.exit(rc)


def config(env):
    return {
        "id": env.get("AWS_BOX_ID") or "i-04d70428c19a484ef",
        "region": env.get("AWS_BOX_REGION") or "eu-central-1",
        "ssh_host": env.get("AWS_BOX_SSH_HOST") or "racecast-box-aws",
        "ssh_user": env.get("AWS_BOX_SSH_USER") or "racecast",
        "ssh_key": env.get("AWS_BOX_SSH_KEY")
        or os.path.join(os.path.expanduser("~"), ".ssh", "racecast-box.pem"),
    }


def describe_argv(cfg, query):
    return ["aws", "ec2", "describe-instances", "--instance-ids", cfg["id"],
            "--region", cfg["region"], "--query", query, "--output", "text"]


def q(h, cfg, query):
    return h.capture(describe_argv(cfg, query), stderr=subprocess.DEVNULL)


def state(h, cfg):
    return q(h, cfg, "Reservations[0].Instances[0].State.Name")


def public_ip(h, cfg):
    _, ip = q(h, cfg, "Reservations[0].Instances[0].PublicIpAddress")
    return "" if ip == "None" else ip


def status(h, cfg):
    rc, st = state(h, cfg)
    if rc:
        die(f"cannot reach AWS (check 'aws configure' / SSO login for region {cfg['region']})")
    if not st:
        die(f"instance {cfg['id']} not found in {cfg['region']}")
    rc, ty = q(h, cfg, "Reservations[0].Instances[0].InstanceType")
    strict(rc)
    print(f"AWS box {cfg['id']} ({ty}, {cfg['region']})")
    print(f"  state:     {st}")
    if st == "running":
        ip = public_ip(h, cfg)
        print(f"  public IP: {ip or '<none yet>'}")
    print(f"  tailnet:   ssh -i {cfg['ssh_key']} {cfg['ssh_user']}@{cfg['ssh_host']}"
          "   (stable across stop/start)")


def start(h, cfg, arg):
    wait = arg != "--no-wait"
    rc, st = state(h, cfg)
    strict(rc)
    if st == "running":
        print("already running.")
        status(h, cfg)
        return
    print(f"starting {cfg['id']} …")
    strict(h.run(["aws", "ec2", "start-instances", "--instance-ids", cfg["id"],
                  "--region", cfg["region"]], stdout=subprocess.DEVNULL))
    if wait:
        print("waiting for it to reach 'running' …")
        strict(h.run(["aws", "ec2", "wait", "instance-running", "--instance-ids", cfg["id"],
                      "--region", cfg["region"]]))
    status(h, cfg)
    print("note: the desktop session + Tailscale take ~1 more minute after 'running'.")


def stop(h, cfg):
    rc, st = state(h, cfg)
    strict(rc)
    if st == "stopped":
        print("already stopped.")
        return
    print(f"stopping {cfg['id']} …")
    strict(h.run(["aws", "ec2", "stop-instances", "--instance-ids", cfg["id"],
                  "--region", cfg["region"]], stdout=subprocess.DEVNULL))
    print("stop requested: billing drops to the EBS boot disk once it reaches 'stopped'.")


def main(argv=None, env=None, h=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    h = h or Host()
    cfg = config(env)
    if not h.which("aws"):
        die("the 'aws' CLI is not installed (brew install awscli)")
    action = argv[0] if argv and argv[0] else "status"
    if action == "status":
        status(h, cfg)
    elif action == "start":
        start(h, cfg, argv[1] if len(argv) > 1 else "")
    elif action == "stop":
        stop(h, cfg)
    elif action == "ip":
        ip = public_ip(h, cfg)
        if not ip:
            die("no public IP (box not running?)")
        print(ip)
    elif action == "ssh":
        if not h.isfile(cfg["ssh_key"]):
            die(f"SSH key not found: {cfg['ssh_key']} (set AWS_BOX_SSH_KEY)")
        h.exec(["ssh", "-i", cfg["ssh_key"], f"{cfg['ssh_user']}@{cfg['ssh_host']}"] + argv[1:])
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
