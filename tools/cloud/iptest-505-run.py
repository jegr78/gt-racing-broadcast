#!/usr/bin/env python3
"""iptest-505-run.py. Laptop orchestrator for the #505 YouTube multi-feed CONCURRENCY
measurement. Pairs with provision-iptest.py (the on-box provisioner).

Does the whole run deterministically. No ad-hoc SSH: launch a persistent throwaway box
(m5.large, non-burstable -> no CPU-credit confound), provision it faithfully WITH the
harness (provision-iptest.py IPTEST_HARNESS=1: real install-tools toolchain + real
cookies + racecast SSH key + tools/multifeed-429-probe.py + src/), then run the #505
cell matrix (sanity -> 2/3-feed burst/staggered -> recovery), each detached on the box and
polled to completion, honouring a cool-down after any cell that threw a 429. Finally it
folds every cell's results.jsonl into the matrix table and (optionally) tears the box down.

Prereq: the YouTube AWS-range bot-check must be OFF (run provision-iptest first / a fresh
box resolves). If resolves BOT-CHECK, the concurrency question can't be reached. Wait for
an off window (see iptest-regions.py).

Config (env):
  IPTEST_COOKIES   local yt-cookies.txt (REQUIRED; from `racecast cookies`)
  IPTEST_HOST      racecast@<ip> of an ALREADY-provisioned box. Skips launch+provision+teardown
  IPTEST_REGION    AWS region for a fresh box (default eu-central-1, the real box's region)
  IPTEST_TYPE      instance type (default m5.large)
  IPTEST_KEY       SSH private key (default ~/.ssh/racecast-box.pem)
  IPTEST_SURVIVAL  per-cell survival window seconds (default 1200 = 20 min)
  IPTEST_COOLDOWN  cool-down seconds after a 429 cell (default 600 = 10 min)
  IPTEST_TEARDOWN  1 = terminate the launched box at the end (default 1; ignored if IPTEST_HOST)
  IPTEST_URLS      space-separated distinct live URLs (default: 3 always-live public channels)
  IPTEST_CELLS     space-separated cell keys to run, in order (default: the full matrix
                   "sanity 2-burst 2-stagger 3-burst 3-stagger recovery"). Quick look:
                   IPTEST_CELLS="sanity 2-burst 2-stagger".
"""
import os, shutil, subprocess, sys, tempfile, time

KN = "iptest-505"
SGN = "iptest-505-sg"
DEFAULT_URLS = ("https://www.youtube.com/@SkyNews/live https://www.youtube.com/@NASA/live "
                "https://www.youtube.com/@aljazeeraenglish/live")
DEFAULT_CELLS = "sanity 2-burst 2-stagger 3-burst 3-stagger recovery"
AMI_FILTER = "Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*"
AMI_QUERY = "reverse(sort_by(Images,&CreationDate))[0].ImageId"
CELLS = {
    "sanity": ("yt-1-sanity", ["--n", "1", "--duration-s", "180"]),
    "2-burst": ("yt-2-burst", ["--n", "2", "--activation", "burst"]),
    "2-stagger": ("yt-2-stagger", ["--n", "2", "--activation", "staggered", "--hls-live-edge", "6"]),
    "3-burst": ("yt-3-burst", ["--n", "3", "--activation", "burst"]),
    "3-stagger": ("yt-3-stagger", ["--n", "3", "--activation", "staggered", "--hls-live-edge", "6"]),
    "recovery": ("yt-2-recovery-backoff", ["--n", "2", "--activation", "burst", "--retry-mode", "backoff"]),
}
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


class Host:
    """Process and filesystem access for the real run; the tests substitute a recorder."""

    def isfile(self, path):
        return os.path.isfile(path)

    def sleep(self, seconds):
        time.sleep(seconds)

    def tempfile(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        return path

    def remove(self, path):
        try:
            os.remove(path)
        except OSError:
            pass  # rm -f: a missing file is fine

    def _argv(self, argv):
        return [shutil.which(argv[0]) or argv[0]] + list(argv[1:])

    def run(self, argv, stdout=None, stderr=None):
        """Run argv with inherited stdio unless redirected (stdout may be a path); return its status."""
        sys.stdout.flush(); sys.stderr.flush()
        try:
            if isinstance(stdout, str):
                with open(stdout, "wb") as f:
                    return subprocess.run(self._argv(argv), stdout=f, stderr=stderr).returncode
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


def config(env, here):
    return {
        "provision": os.path.join(here, "provision-iptest.py"),
        "cookies": env.get("IPTEST_COOKIES") or "",
        "region": env.get("IPTEST_REGION") or "eu-central-1",
        "itype": env.get("IPTEST_TYPE") or "m5.large",
        "key": env.get("IPTEST_KEY") or os.path.join(os.path.expanduser("~"), ".ssh", "racecast-box.pem"),
        "survival": int(env.get("IPTEST_SURVIVAL") or "1200"),
        "cooldown": int(env.get("IPTEST_COOLDOWN") or "600"),
        "urls": env.get("IPTEST_URLS") or DEFAULT_URLS,
        "host": env.get("IPTEST_HOST") or "",
        "teardown": env.get("IPTEST_TEARDOWN") or "1",
        "cells": env.get("IPTEST_CELLS") or DEFAULT_CELLS,
    }


def sshopt(key):
    return ["-i", key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]


def provision_cmd(urls):
    first = urls.split(" ", 1)[0]
    return (f"sudo IPTEST_COOKIES=/tmp/yt-cookies.txt IPTEST_HARNESS=1 IPTEST_URLS='{first}' "
            "python3 /tmp/provision-iptest.py 2>&1 | grep -E 'OK|RESOLVED|BOT-CHECK|OTHER|MISSING|harness'")


def cell_launch_cmd(cell, extra, survival):
    return ("cd ~/iro505 && export PATH=$HOME/runtime/bin:$PATH && mkdir -p probe-runs &&     "
            "nohup python3 tools/multifeed-429-probe.py --urls urls-yt.txt --cookies $HOME/runtime/yt-cookies.txt"
            f"       --out probe-runs --duration-s {survival} --cell-id {cell} {extra}"
            f" > probe-runs/{cell}.console 2>&1 & echo launched pid=$!")


def cell_done_cmd(cell):
    return f"grep -q '# done' ~/iro505/probe-runs/{cell}.console 2>/dev/null"


def cell_record_cmd(cell):
    return f"grep '\"kind\": \"cell\"' ~/iro505/probe-runs/{cell}/results.jsonl 2>/dev/null"


def urls_cmd(urls):
    return f"cd ~/iro505 && printf '%s\\n' {urls} > urls-yt.txt && echo urls: $(wc -l < urls-yt.txt)"


class Run:
    def __init__(self, h, cfg):
        self.h, self.cfg = h, cfg
        self.iid = ""
        self.host = cfg["host"]
        self.ssh = sshopt(cfg["key"])

    def launch_and_provision(self):
        h, cfg, r = self.h, self.cfg, self.cfg["region"]
        pub = h.tempfile()
        h.run(["ssh-keygen", "-y", "-f", cfg["key"]], stdout=pub)
        _, myip = h.capture(["curl", "-s", "https://api.ipify.org"])
        cidr = f"{myip}/32"
        _, ami = h.capture(["aws", "ec2", "describe-images", "--region", r, "--owners", "099720109477",
                            "--filters", AMI_FILTER, "Name=state,Values=available",
                            "--query", AMI_QUERY, "--output", "text"])
        print(f"region={r} type={cfg['itype']} ami={ami} ssh-from={cidr}")
        h.run(["aws", "ec2", "delete-key-pair", "--region", r, "--key-name", KN], **QUIET)
        h.run(["aws", "ec2", "import-key-pair", "--region", r, "--key-name", KN,
               "--public-key-material", f"fileb://{pub}"], **QUIET)
        _, sg = h.capture(["aws", "ec2", "create-security-group", "--region", r, "--group-name", SGN,
                           "--description", "iptest-505", "--query", "GroupId", "--output", "text"],
                          stderr=subprocess.DEVNULL)
        if not sg or sg == "None":
            _, sg = h.capture(["aws", "ec2", "describe-security-groups", "--region", r, "--group-names", SGN,
                               "--query", "SecurityGroups[0].GroupId", "--output", "text"])
        h.run(["aws", "ec2", "authorize-security-group-ingress", "--region", r, "--group-id", sg,
               "--protocol", "tcp", "--port", "22", "--cidr", cidr], **QUIET)
        _, self.iid = h.capture(["aws", "ec2", "run-instances", "--region", r, "--image-id", ami,
                                 "--instance-type", cfg["itype"], "--key-name", KN, "--security-group-ids", sg,
                                 "--associate-public-ip-address",
                                 "--block-device-mappings", "DeviceName=/dev/sda1,Ebs={VolumeSize=20}",
                                 "--tag-specifications",
                                 "ResourceType=instance,Tags=[{Key=purpose,Value=iptest-505}]",
                                 "--query", "Instances[0].InstanceId", "--output", "text"])
        if not self.iid.startswith("i-"):
            print(f"launch failed: {self.iid}")
            h.remove(pub)
            sys.exit(1)
        h.run(["aws", "ec2", "wait", "instance-running", "--region", r, "--instance-ids", self.iid])
        _, ip = h.capture(["aws", "ec2", "describe-instances", "--region", r, "--instance-ids", self.iid,
                           "--query", "Reservations[0].Instances[0].PublicIpAddress", "--output", "text"])
        print(f"IID={self.iid} IP={ip}: waiting for SSH")
        for _ in range(15):
            if h.run(["ssh", *self.ssh, f"ubuntu@{ip}", "true"], stderr=subprocess.DEVNULL) == 0:
                break
            h.sleep(10)
        print("=== provision (toolchain + cookies + racecast key + harness) ===")
        h.run(["scp", *self.ssh, cfg["cookies"], f"ubuntu@{ip}:/tmp/yt-cookies.txt"], **QUIET)
        h.run(["scp", *self.ssh, cfg["provision"], f"ubuntu@{ip}:/tmp/provision-iptest.py"], **QUIET)
        h.run(["ssh", *self.ssh, f"ubuntu@{ip}", provision_cmd(cfg["urls"])])
        h.remove(pub)
        self.host = f"racecast@{ip}"

    def run_cell(self, cell, args):
        h, survival = self.h, self.cfg["survival"]
        extra = " ".join(args)
        print(f">>> cell {cell}  ({extra})  survival={survival}s")
        h.run(["ssh", *self.ssh, self.host, cell_launch_cmd(cell, extra, survival)])
        waited = 0
        while h.run(["ssh", *self.ssh, self.host, cell_done_cmd(cell)]) != 0:
            h.sleep(20)
            waited += 20
            if waited > survival + 600:
                print(f"  timeout waiting for {cell}")
                break
        _, rec = h.capture(["ssh", *self.ssh, self.host, cell_record_cmd(cell)])
        print(f"  {rec}")
        if '"threw_429": true' in rec:
            print(f"  -> 429: cooling down {self.cfg['cooldown']}s before next cell")
            h.sleep(self.cfg["cooldown"])

    def main(self):
        h, cfg = self.h, self.cfg
        if not self.host:
            self.launch_and_provision()
        else:
            print(f"using existing host {self.host}")
        h.run(["ssh", *self.ssh, self.host, urls_cmd(cfg["urls"])])
        print(f"===================== #505 YT CONCURRENCY MATRIX ({self.host}) =====================")
        for c in cfg["cells"].split():
            if c in CELLS:
                self.run_cell(*CELLS[c])
            else:
                print(f"  unknown cell key: {c} (skipped)")
        print("===================== RESULTS MATRIX =====================")
        h.run(["ssh", *self.ssh, self.host,
               "cd ~/iro505 && python3 tools/multifeed-429-probe.py --summarize probe-runs/*/results.jsonl"])
        print("--- raw results tarball on box: ~/iro505/probe-runs (scp back if needed) ---")
        r = cfg["region"]
        if self.iid and cfg["teardown"] == "1":
            print(f"=== teardown {self.iid} ===")
            h.run(["aws", "ec2", "terminate-instances", "--region", r, "--instance-ids", self.iid], **QUIET)
            h.run(["aws", "ec2", "wait", "instance-terminated", "--region", r, "--instance-ids", self.iid],
                  stderr=subprocess.DEVNULL)
            h.run(["aws", "ec2", "delete-security-group", "--region", r, "--group-name", SGN], **QUIET)
            h.run(["aws", "ec2", "delete-key-pair", "--region", r, "--key-name", KN], **QUIET)
            print("torn down + cleaned")
        elif self.iid:
            print(f"box {self.iid} left RUNNING (IPTEST_TEARDOWN=0). Stop it: aws ec2 terminate-instances "
                  f"--region {r} --instance-ids {self.iid}")
        print("===== #505 run done =====")


def main(env=None, h=None, here=None):
    env = os.environ if env is None else env
    h = h or Host()
    cfg = config(env, here or os.path.dirname(os.path.abspath(__file__)))
    if not (cfg["cookies"] and h.isfile(cfg["cookies"])):
        print("set IPTEST_COOKIES to a real yt-cookies.txt", file=sys.stderr)
        sys.exit(1)
    if not h.isfile(cfg["provision"]):
        print(f"missing {cfg['provision']}", file=sys.stderr)
        sys.exit(1)
    run = Run(h, cfg)
    try:
        run.main()
    except KeyboardInterrupt:
        print(f"interrupted: box {run.iid} left running for inspection")
        sys.exit(130)


def _harden_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass  # not a reconfigurable text stream; keep it as it is


if __name__ == "__main__":
    _harden_stdio()
    main()
