#!/usr/bin/env python3
"""iptest-regions.py. Orchestrator for the #505 YouTube-egress-IP test. Runs on YOUR laptop.

For each AWS region it launches a THROWAWAY Ubuntu 24.04 t3.small (no GPU), uploads the
real yt-cookies.txt + provision-iptest.py, runs the minimal provision (racecast install-tools
+ cookie copy + the exact relay resolve command), prints the RESOLVED/BOT-CHECK verdict for
that region's egress IP, then tears the box down (instance + security group + key). Cheap
(t3.small ~ $0.02/h, up for a few minutes) and self-cleaning.

Answers: is the YouTube block AWS-wide, or specific to the eu-central-1 range the event box
used (possibly reputation-burned by the 24 h event)? A region that RESOLVES = relocate there.

Config (env overrides):
  IPTEST_REGIONS   space-separated regions (default: eu-central-1 eu-west-1 us-east-1)
  IPTEST_COOKIES   local yt-cookies.txt to upload (REQUIRED; from `racecast cookies`)
  IPTEST_URLS      space-separated YouTube URLs to resolve (passed to provision-iptest.py)
  IPTEST_KEY       local SSH private key to use/derive a pubkey from (default ~/.ssh/racecast-box.pem)
  IPTEST_SSH_CIDR  CIDR allowed to SSH the throwaway boxes (default: this laptop's /32)
  IPTEST_TYPE      instance type (default t3.small)
"""
import os, shutil, subprocess, sys, tempfile, time

KN = "iptest-key"
SGN = "iptest-sg"
AMI_FILTER = "Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*"
AMI_QUERY = "reverse(sort_by(Images,&CreationDate))[0].ImageId"
QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
VERDICT_LINES = "egress IP|RESOLVED|BOT-CHECK|OTHER|---|install-tools|MISSING|cookies copied"


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
        "regions": env.get("IPTEST_REGIONS") or "eu-central-1 eu-west-1 us-east-1",
        "cookies": env.get("IPTEST_COOKIES") or "",
        "urls": env.get("IPTEST_URLS") or "https://www.youtube.com/@SkyNews/live",
        "key": env.get("IPTEST_KEY") or os.path.join(os.path.expanduser("~"), ".ssh", "racecast-box.pem"),
        "itype": env.get("IPTEST_TYPE") or "t3.small",
        "ssh_cidr": env.get("IPTEST_SSH_CIDR") or "",
    }


def sshopt(key):
    return ["-i", key, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]


def provision_cmd(urls):
    return (f"sudo IPTEST_URLS='{urls}' IPTEST_COOKIES=/tmp/yt-cookies.txt python3 /tmp/provision-iptest.py "
            f"2>&1 | grep -E '{VERDICT_LINES}'")


def test_region(h, cfg, region, pubkey, cidr, ssh):
    print(f"======================== REGION {region} ========================")
    _, ami = h.capture(["aws", "ec2", "describe-images", "--region", region, "--owners", "099720109477",
                        "--filters", AMI_FILTER, "Name=state,Values=available",
                        "--query", AMI_QUERY, "--output", "text"], stderr=subprocess.DEVNULL)
    if not ami or ami == "None":
        print(f"no Ubuntu 24.04 AMI in {region}. Skip")
        return

    h.run(["aws", "ec2", "delete-key-pair", "--region", region, "--key-name", KN], **QUIET)
    h.run(["aws", "ec2", "import-key-pair", "--region", region, "--key-name", KN,
           "--public-key-material", f"fileb://{pubkey}"], **QUIET)
    _, sg = h.capture(["aws", "ec2", "create-security-group", "--region", region, "--group-name", SGN,
                       "--description", "iptest throwaway", "--query", "GroupId", "--output", "text"],
                      stderr=subprocess.DEVNULL)
    if not sg or sg == "None":
        _, sg = h.capture(["aws", "ec2", "describe-security-groups", "--region", region, "--group-names", SGN,
                           "--query", "SecurityGroups[0].GroupId", "--output", "text"],
                          stderr=subprocess.DEVNULL)
    h.run(["aws", "ec2", "authorize-security-group-ingress", "--region", region, "--group-id", sg,
           "--protocol", "tcp", "--port", "22", "--cidr", cidr], **QUIET)

    _, iid = h.capture(["aws", "ec2", "run-instances", "--region", region, "--image-id", ami,
                        "--instance-type", cfg["itype"], "--key-name", KN, "--security-group-ids", sg,
                        "--associate-public-ip-address", "--query", "Instances[0].InstanceId",
                        "--output", "text"], stderr=subprocess.STDOUT)
    if not iid.startswith("i-"):
        print(f"launch failed in {region}: {iid}")
        h.run(["aws", "ec2", "delete-security-group", "--region", region, "--group-id", sg], **QUIET)
        h.run(["aws", "ec2", "delete-key-pair", "--region", region, "--key-name", KN], **QUIET)
        return
    h.run(["aws", "ec2", "wait", "instance-running", "--region", region, "--instance-ids", iid],
          stderr=subprocess.DEVNULL)
    _, ip = h.capture(["aws", "ec2", "describe-instances", "--region", region, "--instance-ids", iid,
                       "--query", "Reservations[0].Instances[0].PublicIpAddress", "--output", "text"])
    print(f"instance {iid} @ {ip}. Waiting for SSH")

    up = False
    for _ in range(12):
        if h.run(["ssh", *ssh, f"ubuntu@{ip}", "true"], stderr=subprocess.DEVNULL) == 0:
            up = True
            break
        h.sleep(10)
    if up:
        h.run(["scp", *ssh, cfg["cookies"], f"ubuntu@{ip}:/tmp/yt-cookies.txt"], **QUIET)
        h.run(["scp", *ssh, cfg["provision"], f"ubuntu@{ip}:/tmp/provision-iptest.py"], **QUIET)
        h.run(["ssh", *ssh, f"ubuntu@{ip}", provision_cmd(cfg["urls"])])
    else:
        print(f"SSH never came up in {region}")

    print(f"--- teardown {region} ---")
    h.run(["aws", "ec2", "terminate-instances", "--region", region, "--instance-ids", iid], **QUIET)
    h.run(["aws", "ec2", "wait", "instance-terminated", "--region", region, "--instance-ids", iid],
          stderr=subprocess.DEVNULL)
    h.run(["aws", "ec2", "delete-security-group", "--region", region, "--group-id", sg], **QUIET)
    h.run(["aws", "ec2", "delete-key-pair", "--region", region, "--key-name", KN], **QUIET)
    print(f"{region} done + cleaned")


def main(env=None, h=None, here=None):
    env = os.environ if env is None else env
    h = h or Host()
    cfg = config(env, here or os.path.dirname(os.path.abspath(__file__)))
    if not h.isfile(cfg["provision"]):
        print(f"missing {cfg['provision']}", file=sys.stderr)
        sys.exit(1)
    if not (cfg["cookies"] and h.isfile(cfg["cookies"])):
        print("set IPTEST_COOKIES to a real yt-cookies.txt", file=sys.stderr)
        sys.exit(1)
    if not h.isfile(cfg["key"]):
        print(f"missing SSH key {cfg['key']}", file=sys.stderr)
        sys.exit(1)

    pubkey = h.tempfile()
    if h.run(["ssh-keygen", "-y", "-f", cfg["key"]], stdout=pubkey, stderr=subprocess.DEVNULL):
        print(f"cannot derive pubkey from {cfg['key']}", file=sys.stderr)
        sys.exit(1)
    _, myip = h.capture(["curl", "-s", "https://api.ipify.org"])
    cidr = cfg["ssh_cidr"] or f"{myip}/32"
    ssh = sshopt(cfg["key"])

    print(f"regions: {cfg['regions']} | type: {cfg['itype']} | ssh-from: {cidr} | urls: {cfg['urls']}")
    for region in cfg["regions"].split():
        test_region(h, cfg, region, pubkey, cidr, ssh)
    h.remove(pubkey)
    print("===== ALL REGIONS DONE =====")


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
