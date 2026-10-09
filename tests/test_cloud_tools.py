#!/usr/bin/env python3
"""Stdlib checks for the cloud-box scripts in tools/cloud/. The expected argv, file contents
and messages are the ones the former bash scripts produced. Run: python3 tests/test_cloud_tools.py"""
import ast, contextlib, importlib.util, io, os, re, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLOUD = os.path.join(ROOT, "tools", "cloud")
DEVNULL = -3  # subprocess.DEVNULL
STDOUT = -2   # subprocess.STDOUT


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(CLOUD, name + ".py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


aws = _load("aws-box")
gcp = _load("gcp-box")
prep = _load("prepare-event")
ipt = _load("provision-iptest")
r505 = _load("iptest-505-run")
regions = _load("iptest-regions")
prov = _load("provision")
X86 = types.SimpleNamespace(machine=lambda: "x86_64")
ipt.platform = X86
prov.platform = X86


class FakeHost:
    """Records every command; answers from rules keyed by an argv prefix tuple or a substring."""

    def __init__(self, rules=(), which=(), files=None, execs=(), dirs=(), links=None, tty=False, answers=(),
                 homes=None):
        self.rules = list(rules)
        self.homes = {"racecast": "/home/racecast", "ubuntu": "/home/ubuntu"} if homes is None else homes
        self._which = set(which)
        self.files = dict(files or {})
        self.execs = set(execs)
        self.dirs = set(dirs)
        self.links = dict(links or {})
        self.tty = tty
        self.answers = list(answers)
        self.calls = []

    def _answer(self, argv):
        for key, rc, out in self.rules:
            if (isinstance(key, tuple) and tuple(argv[:len(key)]) == key) or \
                    (isinstance(key, str) and key in " ".join(argv)):
                return rc, out
        return 0, ""

    def run(self, argv, stdout=None, stderr=None, env=None):
        self.calls.append(("run", list(argv), stdout, stderr))
        return self._answer(argv)[0]

    def capture(self, argv, stderr=None, env=None):
        self.calls.append(("capture", list(argv), stderr, env))
        rc, out = self._answer(argv)
        return rc, out.rstrip("\n")

    def count_bytes(self, argv):
        self.calls.append(("count", list(argv)))
        rc, out = self._answer(argv)
        return rc, len(out)

    def exec(self, argv):
        self.calls.append(("exec", list(argv)))
        raise SystemExit(0)

    def which(self, name):
        return "/usr/bin/" + name if name in self._which else None

    def isfile(self, path):
        return path in self.files

    def exists(self, path):
        return path in self.files or path in self.dirs

    def isexec(self, path):
        return path in self.execs

    def size(self, path):
        return len(self.files[path]) if path in self.files else None

    def read(self, path):
        return self.files.get(path)

    def read_bytes(self, path):
        data = self.files.get(path)
        return data.encode() if isinstance(data, str) else data

    def write(self, path, text):
        self.calls.append(("write", path))
        self.files[path] = text

    def home(self, user):
        return self.homes.get(user)

    def realpath(self, path):
        return self.links.get(path, path)

    def isatty(self):
        return self.tty

    def ask(self, prompt):
        self.calls.append(("ask", prompt))
        return self.answers.pop(0) if self.answers else ""

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))

    def tempfile(self):
        return "/tmp/pubkey"

    def remove(self, path):
        self.calls.append(("remove", path))

    def argvs(self, kind=None):
        return [c[1] for c in self.calls if c[0] in (kind or ("run", "capture", "count", "exec"))]


def run_main(fn, *a, **kw):
    """Call fn; return (exit code, stdout, stderr). A normal return counts as exit 0."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*a, **kw)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- aws-box

AWS_ID = "i-04d70428c19a484ef"


def _aws_describe(query):
    return ["aws", "ec2", "describe-instances", "--instance-ids", AWS_ID, "--region", "eu-central-1",
            "--query", query, "--output", "text"]


def _aws_host(state="running", ip="1.2.3.4", state_rc=0, extra=()):
    return FakeHost(which={"aws"}, rules=list(extra) + [
        ("State.Name", state_rc, state + "\n"), ("InstanceType", 0, "g4dn.xlarge\n"),
        ("PublicIpAddress", 0, ip + "\n")])


def t_aws_config_defaults_and_overrides():
    cfg = aws.config({})
    assert cfg["id"] == AWS_ID and cfg["region"] == "eu-central-1"
    assert cfg["ssh_host"] == "racecast-box-aws" and cfg["ssh_user"] == "racecast"
    assert cfg["ssh_key"] == os.path.join(os.path.expanduser("~"), ".ssh", "racecast-box.pem")
    cfg = aws.config({"AWS_BOX_ID": "i-x", "AWS_BOX_REGION": "", "AWS_BOX_SSH_KEY": "/k"})
    assert cfg["id"] == "i-x" and cfg["region"] == "eu-central-1", "an empty variable falls back like ${VAR:-default}"
    assert cfg["ssh_key"] == "/k"


def t_aws_status_running():
    h = _aws_host()
    code, out, _ = run_main(aws.main, ["status"], {"AWS_BOX_SSH_KEY": "/k"}, h)
    assert code == 0
    assert out == ("AWS box i-04d70428c19a484ef (g4dn.xlarge, eu-central-1)\n  state:     running\n"
                   "  public IP: 1.2.3.4\n  tailnet:   ssh -i /k racecast@racecast-box-aws   (stable across stop/start)\n")
    assert h.argvs() == [_aws_describe("Reservations[0].Instances[0].State.Name"),
                         _aws_describe("Reservations[0].Instances[0].InstanceType"),
                         _aws_describe("Reservations[0].Instances[0].PublicIpAddress")]
    assert all(c[2] == DEVNULL for c in h.calls), "describe-instances runs with 2>/dev/null"


def t_aws_default_action_is_status():
    for argv in ([], [""]):
        code, out, _ = run_main(aws.main, argv, {}, _aws_host(state="stopped"))
        assert code == 0 and "  state:     stopped\n" in out and "public IP" not in out


def t_aws_status_errors():
    code, _, err = run_main(aws.main, ["status"], {}, _aws_host(state_rc=255))
    assert code == 1 and err == "aws-box: cannot reach AWS (check 'aws configure' / SSO login for region eu-central-1)\n"
    code, _, err = run_main(aws.main, ["status"], {}, _aws_host(state=""))
    assert code == 1 and err == f"aws-box: instance {AWS_ID} not found in eu-central-1\n"
    h = _aws_host(extra=[("InstanceType", 7, "")])
    code, out, err = run_main(aws.main, ["status"], {}, h)
    assert (code, out, err) == (7, "", ""), "a failed type lookup ends the script silently with its status (set -e)"


def t_aws_public_ip_none_is_empty():
    code, out, _ = run_main(aws.main, ["status"], {}, _aws_host(ip="None"))
    assert "  public IP: <none yet>\n" in out
    code, out, err = run_main(aws.main, ["ip"], {}, _aws_host(ip="None"))
    assert code == 1 and err == "aws-box: no public IP (box not running?)\n"
    code, out, _ = run_main(aws.main, ["ip"], {}, _aws_host())
    assert (code, out) == (0, "1.2.3.4\n")


def t_aws_start_stopped_waits():
    h = _aws_host(state="stopped")
    code, out, _ = run_main(aws.main, ["start"], {}, h)
    assert code == 0
    runs = [c for c in h.calls if c[0] == "run"]
    assert runs[0][1] == ["aws", "ec2", "start-instances", "--instance-ids", AWS_ID, "--region", "eu-central-1"]
    assert runs[0][2] == DEVNULL
    assert runs[1][1] == ["aws", "ec2", "wait", "instance-running", "--instance-ids", AWS_ID, "--region", "eu-central-1"]
    assert out.startswith(f"starting {AWS_ID} …\nwaiting for it to reach 'running' …\nAWS box")
    assert out.endswith("note: the desktop session + Tailscale take ~1 more minute after 'running'.\n")
    h = _aws_host(state="stopped")
    run_main(aws.main, ["start", "--no-wait"], {}, h)
    assert [c[1][2] for c in h.calls if c[0] == "run"] == ["start-instances"], "--no-wait skips the wait"


def t_aws_start_and_stop_short_circuit():
    code, out, _ = run_main(aws.main, ["start"], {}, _aws_host())
    assert code == 0 and out.startswith("already running.\nAWS box")
    code, out, _ = run_main(aws.main, ["stop"], {}, _aws_host(state="stopped"))
    assert (code, out) == (0, "already stopped.\n")
    h = _aws_host()
    code, out, _ = run_main(aws.main, ["stop"], {}, h)
    assert out == (f"stopping {AWS_ID} …\n"
                   "stop requested: billing drops to the EBS boot disk once it reaches 'stopped'.\n")
    assert h.argvs("run") == [["aws", "ec2", "stop-instances", "--instance-ids", AWS_ID, "--region", "eu-central-1"]]


def t_aws_failed_state_or_start_exits_with_status():
    code, out, _ = run_main(aws.main, ["start"], {}, _aws_host(state_rc=255))
    assert (code, out) == (255, "")
    code, _, _ = run_main(aws.main, ["start"], {}, _aws_host(state="stopped", extra=[("start-instances", 3, "")]))
    assert code == 3


def t_aws_ssh():
    code, _, err = run_main(aws.main, ["ssh", "-v"], {"AWS_BOX_SSH_KEY": "/k"}, _aws_host())
    assert code == 1 and err == "aws-box: SSH key not found: /k (set AWS_BOX_SSH_KEY)\n"
    h = _aws_host()
    h.files["/k"] = "key"
    run_main(aws.main, ["ssh", "-v", "uptime"], {"AWS_BOX_SSH_KEY": "/k", "AWS_BOX_SSH_HOST": "100.64.0.7"}, h)
    assert h.argvs("exec") == [["ssh", "-i", "/k", "racecast@100.64.0.7", "-v", "uptime"]]


def t_aws_unknown_help_and_missing_cli():
    code, _, err = run_main(aws.main, ["bogus"], {}, _aws_host())
    assert code == 1 and err == "aws-box: unknown action 'bogus'. Try: status | start | stop | ip | ssh | help\n"
    code, out, _ = run_main(aws.main, ["help"], {}, _aws_host())
    assert code == 0 and out.startswith("aws-box.py. Start / stop / status") and "AWS_BOX_SSH_KEY" in out
    assert "aws-box.sh" not in out
    code, _, err = run_main(aws.main, ["help"], {}, FakeHost())
    assert code == 1 and err == "aws-box: the 'aws' CLI is not installed (brew install awscli)\n", \
        "the CLI check runs before any action, help included"


# ---------------------------------------------------------------- gcp-box

def _gcp_host(state="RUNNING", ip="34.1.2.3", state_rc=0, extra=()):
    return FakeHost(which={"gcloud"}, rules=list(extra) + [
        ("value(status)", state_rc, state + "\n"), ("basename", 0, "g2-standard-8\n"), ("natIP", 0, ip + "\n")])


def t_gcp_describe_argv_and_project():
    cfg = gcp.config({})
    assert gcp.describe_argv(cfg, "status") == ["gcloud", "compute", "instances", "describe", "racecast-box",
                                                "--zone", "europe-west4-b", "--format=value(status)"]
    cfg = gcp.config({"GCP_BOX_PROJECT": "proj", "GCP_BOX_NAME": "n", "GCP_BOX_ZONE": "z"})
    assert gcp.describe_argv(cfg, "status") == ["gcloud", "compute", "instances", "describe", "n", "--zone", "z",
                                                "--project", "proj", "--format=value(status)"]


def t_gcp_status_and_ip():
    code, out, _ = run_main(gcp.main, [], {}, _gcp_host())
    assert code == 0 and out == (
        "GCP box racecast-box (g2-standard-8, europe-west4-b)\n  state:       RUNNING\n  external IP: 34.1.2.3\n"
        "  ssh:         gcloud compute ssh racecast@racecast-box --zone europe-west4-b   (tailnet 100.x is stable)\n")
    code, _, err = run_main(gcp.main, ["status"], {}, _gcp_host(state_rc=1))
    assert code == 1 and err == "gcp-box: cannot reach GCP (check 'gcloud auth login' / project)\n"
    code, _, err = run_main(gcp.main, ["ip"], {}, _gcp_host(ip=""))
    assert code == 1 and err == "gcp-box: no external IP (box not running?)\n"
    code, out, err = run_main(gcp.main, ["ip"], {}, _gcp_host(extra=[("natIP", 4, "")]))
    assert code == 1 and err == "gcp-box: no external IP (box not running?)\n", "a failed IP lookup counts as no IP"
    code, out, _ = run_main(gcp.main, ["status"], {}, _gcp_host(extra=[("natIP", 4, "")]))
    assert code == 0 and "  external IP: <none>\n" in out


def t_gcp_start_stop_ssh():
    h = _gcp_host(state="TERMINATED")
    code, out, _ = run_main(gcp.main, ["start"], {"GCP_BOX_PROJECT": "p"}, h)
    assert code == 0 and out.startswith("starting racecast-box …\nGCP box")
    assert h.argvs("run") == [["gcloud", "compute", "instances", "start", "racecast-box", "--zone",
                               "europe-west4-b", "--project", "p"]]
    code, out, _ = run_main(gcp.main, ["stop"], {}, _gcp_host(state="TERMINATED"))
    assert out == "already stopped (TERMINATED).\n"
    h = _gcp_host()
    code, out, _ = run_main(gcp.main, ["stop"], {}, h)
    assert out == "stopping racecast-box …\nstopped: billing drops to the boot disk only.\n"
    h = _gcp_host()
    run_main(gcp.main, ["ssh", "--", "uptime"], {}, h)
    assert h.argvs("exec") == [["gcloud", "compute", "ssh", "racecast@racecast-box", "--zone", "europe-west4-b",
                                "--", "uptime"]]
    code, _, err = run_main(gcp.main, ["status"], {}, FakeHost())
    assert code == 1 and err == "gcp-box: the 'gcloud' CLI is not installed (see cloud SDK)\n"


# ---------------------------------------------------------------- prepare-event

RC_BIN = "/home/racecast/racecast"
READY_FILES = {"/home/racecast/runtime/mylg/GT_Racing_Endurance.import.json": "{}",
               "/home/racecast/runtime/yt-cookies.txt": "c"}


def _prep_host(user="racecast", version="racecast 1.2.3", plist="  other\n* mylg\n", plist_rc=0, preflight=0,
               fail=(), ts="100.64.0.5\n", files=None, tty=False, answers=()):
    rules = [(("id", "-un"), 0, user + "\n"), (("racecast", "profile", "list"), plist_rc, plist),
             (("racecast", "--version"), 0, version + "\n"), (("racecast", "preflight"), preflight, ""),
             (("tailscale", "ip", "-4"), 0, ts)]
    rules += [(tuple(f.split()), 1, "") for f in fail]
    return FakeHost(rules=rules, which={"racecast"}, links={"/usr/bin/racecast": RC_BIN},
                    files=dict(READY_FILES if files is None else files), tty=tty, answers=answers)


def t_prep_args():
    p = prep.Prep(FakeHost(), {}, ["--no-twitch", "lg", "--no-speedtest", "--no-update"])
    p.parse_args()
    assert (p.league, p.no_twitch, p.no_speedtest, p.no_update) == ("lg", True, True, True)
    code, out, _ = run_main(prep.main, ["lg", "--help"], {}, FakeHost())
    assert code == 0 and out == prep.USAGE
    assert out.startswith("Usage: python3 prepare-event.py <league> [--no-twitch] [--no-speedtest] [--no-update]\n")
    assert not hasattr(prep, "Die")
    code, out, err = run_main(prep.main, ["--bogus"], {}, FakeHost())
    assert code == 1 and out == prep.USAGE and err == "\033[1;31m[error]\033[0m unknown flag: --bogus\n"
    code, _, err = run_main(prep.main, ["a", "b"], {}, FakeHost())
    assert code == 1 and err.endswith("unexpected argument: b\n")


def t_prep_league_lookup_uses_last_field_and_pipefail():
    p = prep.Prep(FakeHost(rules=[(("racecast",), 0, "  alpha\n* beta (active)\nbeta\n\n")]), {}, [])
    assert p.is_league_imported("alpha") and p.is_league_imported("beta")
    assert p.is_league_imported("(active)"), "awk $NF takes the last field of '* beta (active)'"
    assert not p.is_league_imported("alph")
    p = prep.Prep(FakeHost(rules=[(("racecast",), 1, "alpha\n")]), {}, [])
    assert not p.is_league_imported("alpha"), "a failing `racecast profile list` fails the pipeline"


def t_prep_ready_sequence():
    h = _prep_host()
    code, out, err = run_main(prep.main, ["mylg"], {}, h)
    assert code == 0 and err == ""
    rc_calls = [a[1:] for a in h.argvs() if a[0] == "racecast"]
    assert rc_calls == [["profile", "list"], ["--version"], ["update"], ["profile", "use", "mylg"],
                        ["cookies", "firefox"], ["cookies", "twitch", "firefox"], ["graphics"], ["media"],
                        ["brands"], ["speedtest"], ["relay", "stop"], ["freeport", "--force"], ["preflight"]]
    quiet = [c for c in h.calls if c[0] == "run" and c[1][1] in ("relay", "freeport")]
    assert all(c[2] == DEVNULL and c[3] == DEVNULL for c in quiet)
    assert "\033[1;34m[prepare]\033[0m profile 'mylg' found; install root /home/racecast\n" in out
    assert "  \033[1;32mOK\033[0m   tailnet joined (100.64.0.5)\n" in out
    assert out.endswith("\n\033[1;34m[prepare]\033[0m READY: go live via Control Center 'Start event' "
                        "or:  racecast event start\n")


def t_prep_skips_and_soft_warnings():
    h = _prep_host(fail=["racecast cookies firefox", "racecast graphics", "racecast relay stop"])
    code, out, err = run_main(prep.main, ["mylg", "--no-twitch", "--no-speedtest", "--no-update"], {}, h)
    assert code == 0
    rc_calls = [a[1:] for a in h.argvs() if a[0] == "racecast"]
    assert ["--version"] not in rc_calls and ["speedtest"] not in rc_calls
    assert ["cookies", "twitch", "firefox"] not in rc_calls
    assert "update: skipped (--no-update)" in out and "Twitch cookies: skipped (--no-twitch)" in out
    assert err.count("[warn]") == 3, "two step warnings plus the closing soft-warning summary"
    assert err.endswith("\033[1;33m[warn]\033[0m 2 soft warning(s) above. Review before going live\n"), \
        "a failed relay stop is silenced (|| true), not a soft warning"


def t_prep_preview_guard():
    h = _prep_host(version="racecast 0.9.0-preview-main")
    code, out, _ = run_main(prep.main, ["mylg"], {}, h)
    assert code == 0 and ["racecast", "update"] not in h.argvs()
    assert ("update: preview build 'racecast 0.9.0-preview-main' kept (no TTY to confirm). Run interactively, "
            "or 'racecast update' to move to stable.") in out
    h = _prep_host(version="racecast 0.9.0-preview-main", tty=True, answers=["Yes"])
    code, out, _ = run_main(prep.main, ["mylg"], {}, h)
    assert ["racecast", "update"] in h.argvs()
    assert ("ask", "         Update to latest STABLE (loses the preview fixes)? [y/N] ") in h.calls
    assert "\033[1;33m[prepare]\033[0m Preview build 'racecast 0.9.0-preview-main' installed (kept for the Linux fixes).\n" in out
    h = _prep_host(version="racecast 0.9.0-preview-main", tty=True, answers=["nope"])
    code, out, _ = run_main(prep.main, ["mylg"], {}, h)
    assert ["racecast", "update"] not in h.argvs() and "update: keeping preview build" in out
    code, _, err = run_main(prep.main, ["mylg"], {}, _prep_host(fail=["racecast update"]))
    assert code == 1 and err.endswith("racecast update failed\n")


def t_prep_guards():
    code, _, err = run_main(prep.main, ["mylg", "--no-update"], {}, _prep_host(user="ubuntu"))
    assert code == 1 and err == ("\033[1;31m[error]\033[0m run as the 'racecast' user (current: 'ubuntu'). "
                                 "Try: sudo -iu racecast python3 prepare-event.py mylg --no-update\n")
    code, _, err = run_main(prep.main, ["mylg"], {"RACECAST_USER": "ops"}, _prep_host(user="ops"))
    assert code == 0
    code, out, err = run_main(prep.main, [], {}, _prep_host())
    assert code == 1 and out == prep.USAGE and err.endswith("missing <league>\n")
    code, _, err = run_main(prep.main, ["nope"], {}, _prep_host())
    assert code == 1 and "profile 'nope' is not imported" in err
    code, _, err = run_main(prep.main, ["mylg"], {}, FakeHost())
    assert code == 1 and "racecast not on PATH. Is this the racecast user on a provisioned box?" in err


def t_prep_readiness_verdicts():
    code, out, err = run_main(prep.main, ["mylg"], {}, _prep_host(files={}, ts="10.0.0.1\n", preflight=3))
    assert code == 1
    assert "  \033[1;31mMISS\033[0m tailnet NOT joined. Run:  sudo tailscale up --ssh --hostname racecast-box\n" in out
    assert "MISS\033[0m OBS collection not localized" in out
    assert "  \033[1;33m--\033[0m   no YouTube cookies yet." in out, "missing cookies only advise"
    assert "preflight reported issues (exit 3)" in out
    assert err.endswith("NOT ready: fix the MISS lines above, then re-run:  python3 prepare-event.py mylg\n")
    files = dict(READY_FILES, **{"/home/racecast/profiles/mylg/profile.env": "NAME=x\nDISCORD_CLIENT_ID=1\n"})
    code, out, _ = run_main(prep.main, ["mylg"], {}, _prep_host(files=files))
    assert code == 0 and "league uses Discord but no voice token" in out
    assert ["find", "/home/racecast/runtime", "-name", "discord-rpc-token.json", "-type", "f"] in \
        _argvs_of(prep.main, ["mylg"], _prep_host(files=files))
    h = _prep_host(files=files)
    h.rules.insert(0, (("find",), 0, "/home/racecast/runtime/mylg/discord-rpc-token.json\n"))
    code, out, _ = run_main(prep.main, ["mylg"], {}, h)
    assert "Discord voice token cached" in out


def _argvs_of(fn, argv, h):
    run_main(fn, argv, {}, h)
    return h.argvs()


# ---------------------------------------------------------------- provision-iptest

def _ipt_host(resolve=(0, "https://rr1.googlevideo.com/videoplayback?x=1\n"), uid="0", files=None, which=(),
              execs=None, extra=(), homes=None):
    rt = "/home/racecast/runtime/bin"
    rules = list(extra) + [
        (("id", "-u"), 0, uid + "\n"), (("id", "-gn"), 0, "racecast\n"),
        ("yt-dlp -g", resolve[0], resolve[1]),
        ((rt + "/streamlink", "--help"), 0, "usage\n  --http-cookies-file FILE\n"),
        ("--version", 0, "1.0\n")]
    files = {"/home/ubuntu/.ssh/authorized_keys": b"k\n", "/tmp/yt-cookies.txt": b"a\nb\nc\n",
             "/home/racecast/runtime/yt-cookies.txt": b"copied"} if files is None else files
    execs = {f"{rt}/{t}" for t in ("yt-dlp", "streamlink", "deno")} if execs is None else execs
    return FakeHost(rules=rules, which=which, files=files, execs=execs, homes=homes)


def t_iptest_resolve_and_pull_argv():
    assert ipt.resolve_argv("racecast", "/r/bin", "/usr/bin", ["--cookies", "/c"], "https://u") == [
        "sudo", "-u", "racecast", "-H", "env", "PATH=/r/bin:/usr/bin", "/r/bin/yt-dlp", "-g", "-f",
        "b[height<=1080]/b", "--no-warnings", "--no-playlist", "--cookies", "/c", "--", "https://u"]
    assert ipt.pull_argv("racecast", "/r/bin", "/usr/bin", "https://m") == [
        "sudo", "-u", "racecast", "-H", "env", "PATH=/r/bin:/usr/bin", "timeout", "10", "/r/bin/streamlink",
        "--stdout", "--http-header", "User-Agent=Mozilla/5.0", "--", "https://m", "best"]


def t_iptest_verdict():
    assert ipt.verdict("https://" + "x" * 100) == "  RESOLVED  -> https://" + "x" * 72 + "…"
    assert ipt.verdict("ERROR: Sign in to confirm you're not a bot") == "  BOT-CHECK -> IP is YouTube-bot-blocked"
    assert ipt.verdict("y" * 200) == "  OTHER     -> " + "y" * 110


def t_iptest_harness_script_matches_bash_bytes():
    assert ipt.harness_script("main") == (
        "\n        cd ~ && curl -fsSL -o iro505.tgz 'https://codeload.github.com/jegr78/gt-racing-broadcast/"
        "tar.gz/refs/heads/main'         && tar xzf iro505.tgz         && ln -sfn \"$(tar tzf iro505.tgz | "
        "head -1 | cut -d/ -f1)\" iro505         && test -f iro505/tools/multifeed-429-probe.py")


def t_iptest_happy_run():
    h = _ipt_host()
    code, out, _ = run_main(ipt.main, {}, h)
    assert code == 0
    a = h.argvs()
    assert ["install", "-d", "-o", "racecast", "-g", "racecast", "-m", "0700", "/home/racecast/.ssh"] in a
    assert ["install", "-m", "0600", "-o", "racecast", "-g", "racecast", "/home/ubuntu/.ssh/authorized_keys",
            "/home/racecast/.ssh/authorized_keys"] in a
    assert ["curl", "-fsSL", "https://github.com/jegr78/gt-racing-broadcast/releases/latest/download/racecast-linux.tar.gz",
            "-o", "/tmp/racecast.tar.gz"] in a
    assert ["ln", "-sf", "/home/racecast/racecast", "/usr/local/bin/racecast"] in a
    resolve = next(x for x in a if ipt.YTDLP_FMT in x)
    assert resolve[-4:] == ["--cookies", "/home/racecast/runtime/yt-cookies.txt", "--",
                            "https://www.youtube.com/@SkyNews/live"]
    assert "cookies copied -> /home/racecast/runtime/yt-cookies.txt (3 lines)" in out
    assert "  RESOLVED  -> https://rr1.googlevideo.com/videoplayback?x=1…\n" in out
    assert out.rstrip().endswith("provision-iptest.py complete. Resolve verdict(s) above are the #505 answer for this IP")


def t_iptest_failed_resolve_prints_the_verdict():
    h = _ipt_host(resolve=(1, "ERROR: [youtube] x: Sign in to confirm you\u2019re not a bot\nmore\n"))
    code, out, _ = run_main(ipt.main, {"IPTEST_URLS": "https://a https://b"}, h)
    assert code == 0, "a failed resolve is a test result, not a reason to stop"
    assert out.count("  BOT-CHECK -> IP is YouTube-bot-blocked\n") == 2, "every URL gets its verdict"
    assert out.rstrip().endswith("are the #505 answer for this IP")


def t_iptest_verdicts_reach_the_runners():
    for line in (ipt.verdict("https://x"), ipt.verdict("not a bot"), ipt.verdict("ERROR: 403")):
        for cmd in (r505.provision_cmd("u"), regions.provision_cmd("u")):
            pattern = cmd.split("grep -E '", 1)[1].rstrip("'")
            assert re.search(pattern, line), f"{line!r} must pass the runner filter {pattern!r}"


def t_iptest_survives_missing_login_user_and_network():
    h = _ipt_host(extra=[(("curl", "-s"), 6, "")], homes={"racecast": "/home/racecast"})
    code, out, _ = run_main(ipt.main, {}, h)
    assert code == 0, "no ubuntu user (GCP) and no network for ipify must not end the run"
    assert "no user ubuntu on this box: racecast SSH access not set up (set SUDO_USER)" in out
    assert "  egress IP (as the internet sees it): <unknown, curl exit 6>\n" in out
    assert not any(a[0] == "install" and "authorized_keys" in a[-1] for a in h.argvs())


def t_iptest_without_cookies_and_not_root():
    h = _ipt_host(files={})
    code, out, _ = run_main(ipt.main, {"IPTEST_URLS": "https://a  https://b"}, h)
    assert code == 0 and "no cookies at /tmp/yt-cookies.txt. Resolve will run WITHOUT --cookies" in out
    resolves = [x for x in h.argvs() if ipt.YTDLP_FMT in x]
    assert [x[-1] for x in resolves] == ["https://a", "https://b"] and all("--cookies" not in x for x in resolves)
    code, _, err = run_main(ipt.main, {}, _ipt_host(uid="1000"))
    assert code == 1 and err == "provision-iptest.py must run as root (sudo)\n"


def t_iptest_pull_and_harness():
    h = _ipt_host(extra=[("streamlink --stdout", 0, "x" * 42)])
    code, out, _ = run_main(ipt.main, {"IPTEST_PULL": "1", "IPTEST_HARNESS": "1", "IPTEST_HARNESS_REF": "r"}, h)
    assert code == 0 and "  pull 10s: 42 bytes\n" in out
    assert ["sudo", "-u", "racecast", "-H", "bash", "-lc", ipt.harness_script("r")] in h.argvs()
    assert "harness at /home/racecast/iro505 (ref r). Src/ + tools/ present" in out
    h = _ipt_host(extra=[("streamlink --stdout", 124, "x" * 9000)])
    code, out, _ = run_main(ipt.main, {"IPTEST_PULL": "1"}, h)
    assert code == 0 and "  pull 10s: 9000 bytes\n" in out, "the 10 s timeout is how the pull ends, not a failure"


# ---------------------------------------------------------------- iptest-505-run

def t_505_remote_commands_match_bash_bytes():
    assert r505.cell_launch_cmd("yt-2-burst", "--n 2 --activation burst", 1200) == (
        "cd ~/iro505 && export PATH=$HOME/runtime/bin:$PATH && mkdir -p probe-runs &&     nohup python3 "
        "tools/multifeed-429-probe.py --urls urls-yt.txt --cookies $HOME/runtime/yt-cookies.txt       --out "
        "probe-runs --duration-s 1200 --cell-id yt-2-burst --n 2 --activation burst > probe-runs/yt-2-burst.console "
        "2>&1 & echo launched pid=$!")
    assert r505.urls_cmd("a b c") == \
        "cd ~/iro505 && printf '%s\\n' a b c > urls-yt.txt && echo urls: $(wc -l < urls-yt.txt)"
    assert r505.cell_record_cmd("x") == "grep '\"kind\": \"cell\"' ~/iro505/probe-runs/x/results.jsonl 2>/dev/null"
    assert r505.cell_done_cmd("x") == "grep -q '# done' ~/iro505/probe-runs/x.console 2>/dev/null"
    assert r505.provision_cmd("https://a https://b") == (
        "sudo IPTEST_COOKIES=/tmp/yt-cookies.txt IPTEST_HARNESS=1 IPTEST_URLS='https://a' "
        "python3 /tmp/provision-iptest.py 2>&1 | grep -E 'OK|RESOLVED|BOT-CHECK|OTHER|MISSING|harness'")


def t_505_config_and_cells():
    cfg = r505.config({"IPTEST_COOKIES": "/c"}, "/here")
    assert cfg["provision"] == os.path.join("/here", "provision-iptest.py")
    assert (cfg["region"], cfg["itype"], cfg["survival"], cfg["cooldown"], cfg["teardown"]) == \
        ("eu-central-1", "m5.large", 1200, 600, "1")
    assert cfg["cells"] == "sanity 2-burst 2-stagger 3-burst 3-stagger recovery"
    assert r505.CELLS["sanity"] == ("yt-1-sanity", ["--n", "1", "--duration-s", "180"])
    assert r505.CELLS["recovery"] == ("yt-2-recovery-backoff",
                                      ["--n", "2", "--activation", "burst", "--retry-mode", "backoff"])


def _505_env(**kw):
    return dict({"IPTEST_COOKIES": "/c", "IPTEST_KEY": "/k"}, **kw)


def t_505_existing_host_runs_cells_and_cools_down():
    h = FakeHost(files={"/c": "", os.path.join("/here", "provision-iptest.py"): ""},
                 rules=[("kind", 0, '{"kind": "cell", "threw_429": true}\n')])
    code, out, _ = run_main(r505.main, _505_env(IPTEST_HOST="racecast@h", IPTEST_CELLS="sanity bogus",
                                                IPTEST_COOLDOWN="5"), h, "/here")
    assert code == 0
    ssh = ["ssh", "-i", "/k", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "racecast@h"]
    remote = [a[-1] for a in h.argvs()]
    assert all(a[:-1] == ssh for a in h.argvs())
    assert remote[0] == r505.urls_cmd(r505.DEFAULT_URLS)
    assert remote[1] == r505.cell_launch_cmd("yt-1-sanity", "--n 1 --duration-s 180", 1200)
    assert ("sleep", 5) in h.calls
    assert "using existing host racecast@h\n" in out and "  unknown cell key: bogus (skipped)\n" in out
    assert "teardown" not in out, "an existing host is never torn down"


def t_505_poll_timeout():
    h = FakeHost(files={"/c": "", os.path.join("/here", "provision-iptest.py"): ""}, rules=[("grep -q", 1, "")])
    code, out, _ = run_main(r505.main, _505_env(IPTEST_HOST="racecast@h", IPTEST_CELLS="sanity",
                                                IPTEST_SURVIVAL="30"), h, "/here")
    assert [c for c in h.calls if c[0] == "sleep"] == [("sleep", 20)] * 32
    assert "  timeout waiting for yt-1-sanity\n" in out


def t_505_launch_provision_teardown():
    h = FakeHost(files={"/c": "", os.path.join("/here", "provision-iptest.py"): ""}, rules=[
        ("describe-images", 0, "ami-1\n"), ("create-security-group", 0, "sg-1\n"),
        ("run-instances", 0, "i-0abc\n"), ("describe-instances", 0, "198.51.100.7\n"),
        (("curl",), 0, "192.0.2.10")])
    code, out, _ = run_main(r505.main, _505_env(IPTEST_CELLS=""), h, "/here")
    assert code == 0
    a = h.argvs()
    assert ["aws", "ec2", "import-key-pair", "--region", "eu-central-1", "--key-name", "iptest-505",
            "--public-key-material", "fileb:///tmp/pubkey"] in a
    assert ["aws", "ec2", "authorize-security-group-ingress", "--region", "eu-central-1", "--group-id", "sg-1",
            "--protocol", "tcp", "--port", "22", "--cidr", "192.0.2.10/32"] in a
    assert a[-4:] == [
        ["aws", "ec2", "terminate-instances", "--region", "eu-central-1", "--instance-ids", "i-0abc"],
        ["aws", "ec2", "wait", "instance-terminated", "--region", "eu-central-1", "--instance-ids", "i-0abc"],
        ["aws", "ec2", "delete-security-group", "--region", "eu-central-1", "--group-name", "iptest-505-sg"],
        ["aws", "ec2", "delete-key-pair", "--region", "eu-central-1", "--key-name", "iptest-505"]]
    assert any(x[-1] == r505.provision_cmd(r505.DEFAULT_URLS) for x in a)
    assert ["scp"] + r505.sshopt("/k") + [os.path.join("/here", "provision-iptest.py"),
                                         "ubuntu@198.51.100.7:/tmp/provision-iptest.py"] in a
    assert "torn down + cleaned\n" in out


def t_505_validation():
    code, _, err = run_main(r505.main, {}, FakeHost(), "/here")
    assert code == 1 and err == "set IPTEST_COOKIES to a real yt-cookies.txt\n"
    code, _, err = run_main(r505.main, {"IPTEST_COOKIES": "/c"}, FakeHost(files={"/c": ""}), "/here")
    assert code == 1 and err == "missing " + os.path.join("/here", "provision-iptest.py") + "\n"


# ---------------------------------------------------------------- iptest-regions

def _regions_host(iid="i-0abc", ami="ami-1"):
    return FakeHost(files={"/c": "", "/k": "", os.path.join("/here", "provision-iptest.py"): ""}, rules=[
        ("describe-images", 0, ami + "\n"), ("create-security-group", 0, "sg-1\n"),
        ("run-instances", 0, iid + "\n"), ("describe-instances", 0, "198.51.100.7\n"),
        (("curl",), 0, "192.0.2.10")])


def t_regions_flow():
    h = _regions_host()
    code, out, _ = run_main(regions.main, {"IPTEST_COOKIES": "/c", "IPTEST_KEY": "/k", "IPTEST_REGIONS": "eu-west-1"},
                            h, "/here")
    assert code == 0
    a = h.argvs()
    assert a[0] == ["ssh-keygen", "-y", "-f", "/k"] and a[1] == ["curl", "-s", "https://api.ipify.org"]
    assert ["aws", "ec2", "run-instances", "--region", "eu-west-1", "--image-id", "ami-1", "--instance-type",
            "t3.small", "--key-name", "iptest-key", "--security-group-ids", "sg-1", "--associate-public-ip-address",
            "--query", "Instances[0].InstanceId", "--output", "text"] in a
    assert any(x[-1] == regions.provision_cmd("https://www.youtube.com/@SkyNews/live") for x in a)
    assert a[-1] == ["aws", "ec2", "delete-key-pair", "--region", "eu-west-1", "--key-name", "iptest-key"]
    assert ["aws", "ec2", "delete-security-group", "--region", "eu-west-1", "--group-id", "sg-1"] in a
    assert out.startswith("regions: eu-west-1 | type: t3.small | ssh-from: 192.0.2.10/32 | urls: ")
    assert out.endswith("eu-west-1 done + cleaned\n===== ALL REGIONS DONE =====\n")
    assert ("remove", "/tmp/pubkey") in h.calls
    assert regions.provision_cmd("u") == ("sudo IPTEST_URLS='u' IPTEST_COOKIES=/tmp/yt-cookies.txt python3 "
                                          "/tmp/provision-iptest.py 2>&1 | grep -E 'egress IP|RESOLVED|BOT-CHECK|"
                                          "OTHER|---|install-tools|MISSING|cookies copied'")


def t_regions_skips_and_failures():
    h = _regions_host(ami="None")
    code, out, _ = run_main(regions.main, {"IPTEST_COOKIES": "/c", "IPTEST_KEY": "/k", "IPTEST_REGIONS": "r1"},
                            h, "/here")
    assert "no Ubuntu 24.04 AMI in r1. Skip\n" in out and not any("run-instances" in x for x in h.argvs())
    h = _regions_host(iid="An error occurred")
    code, out, _ = run_main(regions.main, {"IPTEST_COOKIES": "/c", "IPTEST_KEY": "/k", "IPTEST_REGIONS": "r1",
                                           "IPTEST_SSH_CIDR": "10.0.0.0/8"}, h, "/here")
    assert "launch failed in r1: An error occurred\n" in out and "ssh-from: 10.0.0.0/8" in out
    assert h.argvs()[-2:] == [["aws", "ec2", "delete-security-group", "--region", "r1", "--group-id", "sg-1"],
                              ["aws", "ec2", "delete-key-pair", "--region", "r1", "--key-name", "iptest-key"]]
    launch = next(c for c in h.calls if "run-instances" in c[1])
    assert launch[2] == STDOUT, "the run-instances error text is captured (2>&1)"
    code, _, err = run_main(regions.main, {"IPTEST_COOKIES": "/c"}, _regions_host(), "/here")
    expected_key = os.path.join(os.path.expanduser("~"), ".ssh", "racecast-box.pem")
    assert code == 1 and err == f"missing SSH key {expected_key}\n"
    h = _regions_host()
    h.rules.insert(0, (("ssh-keygen",), 1, ""))
    code, _, err = run_main(regions.main, {"IPTEST_COOKIES": "/c", "IPTEST_KEY": "/k"}, h, "/here")
    assert code == 1 and err == "cannot derive pubkey from /k\n"


# ---------------------------------------------------------------- provision

XORG_EXPECTED = """# racecast headless X (generated by provision.py). Single 1920x1080, no monitor.
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "Device0"
    Driver     "nvidia"
    BusID      "PCI:0:31:0"
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
NV_LSPCI_D = ("0000:00:03.0 Ethernet controller: Google\n"
              "0000:00:1f.0 3D controller: NVIDIA Corporation AD104GL [L4] (rev a1)\n")
HOME = "/home/racecast"


def t_prov_pci_bus_id():
    assert prov.pci_bus_id(NV_LSPCI_D) == "PCI:0:31:0"
    assert prov.pci_bus_id("0000:0a:00.1 VGA compatible controller: NVIDIA T4\n") == "PCI:10:0:1"
    assert prov.pci_bus_id("0000:00:05.0 Audio device: NVIDIA HDA\n") == "", "an NVIDIA non-display device is no GPU"
    assert prov.pci_bus_id("") == ""


def t_prov_has_nvidia_gpu_needs_lspci_success():
    assert prov.has_nvidia_gpu(FakeHost(rules=[(("lspci",), 0, "00:04.0 3D controller: NVIDIA L4\n")]))
    assert not prov.has_nvidia_gpu(FakeHost(rules=[(("lspci",), 0, "00:03.0 Ethernet\n")]))
    assert not prov.has_nvidia_gpu(FakeHost(rules=[(("lspci",), 127, "")]))


def t_prov_generated_files():
    assert prov.xorg_conf("PCI:0:31:0") == XORG_EXPECTED
    assert prov.autologin_conf("racecast") == \
        "[Seat:*]\nautologin-user=racecast\nautologin-user-timeout=0\nuser-session=xfce\n"
    assert prov.app_desktop("OBS Studio (racecast)", HOME + "/.local/bin/racecast-obs-launch") == (
        "[Desktop Entry]\nType=Application\nName=OBS Studio (racecast)\n"
        "Exec=/home/racecast/.local/bin/racecast-obs-launch\nX-GNOME-Autostart-enabled=true\n")
    assert prov.locker_desktop("light-locker") == (
        "[Desktop Entry]\nType=Application\nName=light-locker (disabled by racecast: a passwordless-autologin "
        "box must never lock to the greeter)\nExec=/bin/true\nHidden=true\nX-GNOME-Autostart-enabled=false\n")
    assert prov.sudoers_line("racecast") == "racecast ALL=(ALL) NOPASSWD:ALL\n"
    assert prov.MOZILLA_LIST == ("deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] "
                                 "https://packages.mozilla.org/apt mozilla main\n")
    assert prov.MOZILLA_PIN == "Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n"
    assert prov.OBS_LAUNCH.startswith("#!/bin/sh\n") and prov.OBS_LAUNCH.endswith(
        'rm -f "$HOME/.config/obs-studio/.sentinel/"run_* 2>/dev/null || true\nexec obs "$@"\n')
    assert prov.DISCORD_LAUNCH.endswith('exec discord --password-store=basic "$@"\n')
    assert "ExecStart=/usr/local/sbin/racecast-rustdesk-setup\n" in prov.RUSTDESK_UNIT
    assert "ConditionPathExists=!/var/lib/racecast/rustdesk-configured\n" in prov.RUSTDESK_UNIT


def t_prov_rustdesk_helper_is_the_bash_payload():
    helper = prov.rustdesk_helper("ops")
    assert helper.startswith("#!/usr/bin/env bash\n# Managed by racecast provision.py.")
    assert 'USER_NAME="ops"\n' in helper and "__USER__" not in helper
    assert "    /^\\[options\\]/ && !ins { print; print line; ins=1; next }\n" in helper
    assert 'for cfg in "/root/.config/rustdesk/RustDesk2.toml" \\\n' in helper
    assert helper.endswith("install -d /var/lib/racecast; : > /var/lib/racecast/rustdesk-configured\n")


def t_prov_release_url_and_arch():
    assert prov.release_url("latest", "x86_64") == \
        "https://github.com/jegr78/gt-racing-broadcast/releases/latest/download/racecast-linux.tar.gz"
    assert prov.release_url("preview-main", "aarch64") == \
        "https://github.com/jegr78/gt-racing-broadcast/releases/download/preview-main/racecast-linux-arm64.tar.gz"
    assert prov.cuda_arch("arm64") == "sbsa" and prov.cuda_arch("x86_64") == "x86_64"


def t_prov_rustdesk_deb_per_architecture():
    assert prov.rustdesk_deb_url("1.3.8", "x86_64") == \
        "https://github.com/rustdesk/rustdesk/releases/download/1.3.8/rustdesk-1.3.8-x86_64.deb"
    assert prov.rustdesk_deb_url("1.3.8", "amd64").endswith("/rustdesk-1.3.8-x86_64.deb")
    assert prov.rustdesk_deb_url("1.3.8", "aarch64").endswith("/rustdesk-1.3.8-aarch64.deb")
    assert prov.rustdesk_deb_url("1.3.8", "arm64").endswith("/rustdesk-1.3.8-aarch64.deb")
    assert prov.rustdesk_deb_url("1.3.8", "armv7l") is None
    saved = prov.platform
    prov.platform = types.SimpleNamespace(machine=lambda: "riscv64")
    try:
        code, _, err = run_main(prov.step_rustdesk, FakeHost(), "racecast", {})
    finally:
        prov.platform = saved
    assert code == 1 and err == \
        "provision.py: no RustDesk .deb for architecture 'riscv64' (only x86_64 and aarch64)\n"


def t_cloud_docstrings_carry_no_issue_history():
    for mod in (aws, gcp, prep, ipt, r505, regions, prov):
        refs = set(re.findall(r"#\d+", mod.__doc__)) - {"#505"}
        assert not refs, f"{mod.__name__} docstring cites {refs}: say what it does, not its history"



def t_prov_gui_autostart_files():
    h = FakeHost(rules=[(("id", "-gn"), 0, "rcgrp\n")])
    prov.write_gui_autostart(h, HOME, "racecast")
    a = h.argvs()
    assert a[1] == ["install", "-d", "-o", "racecast", "-g", "rcgrp", HOME + "/.config/autostart", HOME + "/.local/bin"]
    assert ["chmod", "0755", HOME + "/.local/bin/racecast-obs-launch", HOME + "/.local/bin/racecast-discord-launch"] in a
    assert a[-1] == ["chown", "-R", "racecast:rcgrp", HOME + "/.config/autostart", HOME + "/.local/bin"]
    assert h.files[HOME + "/.config/autostart/racecast-discord.desktop"] == prov.app_desktop(
        "Discord (racecast)", HOME + "/.local/bin/racecast-discord-launch")
    assert h.files[HOME + "/.config/autostart/xscreensaver.desktop"] == prov.locker_desktop("xscreensaver")
    assert h.files[HOME + "/.local/bin/racecast-obs-launch"] == prov.OBS_LAUNCH


def _gpu_host(smi=0, xorg=None, extra=()):
    files = {} if xorg is None else {"/etc/X11/xorg.conf": xorg}
    return FakeHost(files=files, rules=list(extra) + [
        (("lspci", "-D"), 0, NV_LSPCI_D), (("lspci",), 0, "00:1f.0 3D controller: NVIDIA L4\n"),
        (("nvidia-smi", "--query-gpu=name", "--format=csv,noheader"), 0, "NVIDIA L4\n"), (("nvidia-smi",), smi, ""),
        (("dkms",), 0, "nvidia/580, 6.17: installed\n")])


def t_prov_xorg_idempotency():
    h = _gpu_host(xorg=b"Section foreign\n")
    code, out, _ = run_main(prov.step_nvidia, h)
    assert "/etc/X11/xorg.conf exists and is not ours. Leaving it untouched" in out
    assert h.files["/etc/X11/xorg.conf"] == b"Section foreign\n"
    h = _gpu_host(xorg=b"# racecast headless X (generated by provision.sh). old\n")
    run_main(prov.step_nvidia, h)
    assert h.files["/etc/X11/xorg.conf"] == XORG_EXPECTED, "a file with our marker is rewritten"
    h = _gpu_host()
    code, out, _ = run_main(prov.step_nvidia, h)
    assert "driver already loaded (NVIDIA L4)" in out and "xorg.conf written (headless 1080p on PCI:0:31:0)" in out


def t_prov_driver_install_path():
    h = _gpu_host(smi=1, extra=[(("apt-key",), 0, "pub\n")])
    code, out, _ = run_main(prov.step_nvidia, h)
    a = h.argvs()
    i = a.index(["apt-key", "list"])
    assert a[i + 1:i + 6] == [
        ["curl", "-fsSL", "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb",
         "-o", "/tmp/cuda-keyring.deb"],
        ["dpkg", "-i", "/tmp/cuda-keyring.deb"], ["apt-get", "update"],
        ["apt-get", "install", "-y", "nvidia-open"], ["ldconfig"]]
    assert "driver module BUILT but not loaded. Reboot, then re-run provision.py" in out
    h = _gpu_host(smi=1, extra=[(("apt-key",), 0, "cuda repo key\n")])
    run_main(prov.step_nvidia, h)
    assert ["dpkg", "-i", "/tmp/cuda-keyring.deb"] not in h.argvs(), "the CUDA key is already trusted"


def t_prov_cpu_dry_run_skips_gpu():
    h = FakeHost(rules=[(("lspci",), 0, "00:03.0 Ethernet\n")])
    code, out, _ = run_main(prov.step_nvidia, h)
    assert "no NVIDIA GPU on this host. Skipping driver + xorg (CPU dry-run mode)" in out
    assert not any(c[0] == "write" for c in h.calls)


def t_prov_rustdesk_password_never_passes_through_python():
    h = FakeHost(which={"rustdesk"})
    run_main(prov.step_rustdesk, h, "racecast", {"RUSTDESK_PASSWORD": "s3cret"})
    cmd = prov.env_or_random_file_argv("/etc/racecast/rustdesk-password", "RUSTDESK_PASSWORD")
    assert cmd in h.argvs() and cmd[:2] == ["sh", "-c"] and cmd[-1] == "/etc/racecast/rustdesk-password"
    assert not any("s3cret" in " ".join(a) for a in h.argvs()), "the password never appears in an argv"
    assert "/etc/racecast/rustdesk-password" not in h.files, "Python never writes the password file"
    assert "umask 077" in cmd[2] and '"$RUSTDESK_PASSWORD"' in cmd[2]


def t_prov_rustdesk_password_file_mode_and_gate():
    if os.name == "nt" or not shutil_which("sh"):
        return  # the generator runs on the Linux box; Windows has no sh
    with tempfile.TemporaryDirectory() as d:
        path = d + "/pw"
        env = dict(os.environ, RUSTDESK_PASSWORD="pw from env")
        assert subprocess.run(prov.env_or_random_file_argv(path, "RUSTDESK_PASSWORD"), env=env).returncode == 0
        assert _read(path) == "pw from env" and (os.stat(path).st_mode & 0o777) == 0o600
        env["RUSTDESK_PASSWORD"] = "other"
        subprocess.run(prov.env_or_random_file_argv(path, "RUSTDESK_PASSWORD"), env=env)
        assert _read(path) == "pw from env", "a non-empty password file is kept"
        _truncate(path)
        env.pop("RUSTDESK_PASSWORD")
        subprocess.run(prov.env_or_random_file_argv(path, "RUSTDESK_PASSWORD"), env=env)
        generated = _read(path)
        assert len(generated) == 16 and generated.isascii() and generated.isalnum(), generated
        assert (os.stat(path).st_mode & 0o777) == 0o600


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _truncate(path):
    with open(path, "w", encoding="utf-8"):
        pass  # an empty file is what the gate test needs


def shutil_which(name):
    import shutil
    return shutil.which(name)


def t_prov_rustdesk_files():
    h = FakeHost(which={"rustdesk"})
    run_main(prov.step_rustdesk, h, "racecast", {})
    assert h.files["/usr/local/sbin/racecast-rustdesk-setup"] == prov.rustdesk_helper("racecast")
    assert h.files["/etc/systemd/system/racecast-rustdesk-setup.service"] == prov.RUSTDESK_UNIT
    h = FakeHost()
    run_main(prov.step_rustdesk, h, "racecast", {"RUSTDESK_VERSION": "1.4.0"})
    assert ["curl", "-fsSL", "https://github.com/rustdesk/rustdesk/releases/download/1.4.0/rustdesk-1.4.0-x86_64.deb",
            "-o", "/tmp/rustdesk-1.4.0.deb"] in h.argvs()


def t_prov_sudoers_validation():
    h = FakeHost()
    code, out, _ = run_main(prov.step_sudo, h, "racecast")
    assert h.files["/etc/sudoers.d/90-racecast"] == "racecast ALL=(ALL) NOPASSWD:ALL\n"
    assert h.argvs() == [["chmod", "0440", "/etc/sudoers.d/90-racecast"], ["visudo", "-cf", "/etc/sudoers.d/90-racecast"]]
    h = FakeHost(rules=[(("visudo",), 1, "")])
    code, out, _ = run_main(prov.step_sudo, h, "racecast")
    assert h.argvs()[-1] == ["rm", "-f", "/etc/sudoers.d/90-racecast"]
    assert "sudoers validation failed: drop-in removed" in out


def t_prov_firefox_snap_detection():
    h = FakeHost(which={"firefox"}, links={"/usr/bin/firefox": "/snap/firefox/123/usr/lib/firefox/firefox"})
    run_main(prov.step_firefox, h)
    assert ["apt-get", "install", "-y", "firefox"] in h.argvs()
    assert h.files["/etc/apt/sources.list.d/mozilla.list"] == prov.MOZILLA_LIST
    h = FakeHost(which={"firefox"})
    code, out, _ = run_main(prov.step_firefox, h)
    assert "non-snap Firefox already present" in out and h.argvs() == []


def t_prov_racecast_install_and_dirs():
    h = FakeHost(rules=[(("id", "-gn"), 0, "rc\n")])
    run_main(prov.step_racecast, h, "racecast", {"RACECAST_TAG": ""})
    a = h.argvs()
    assert ["curl", "-fsSL", "https://github.com/jegr78/gt-racing-broadcast/releases/latest/download/racecast-linux.tar.gz",
            "-o", "/tmp/racecast.tar.gz"] in a, "an empty RACECAST_TAG means latest"
    assert ["tar", "-xzf", "/tmp/racecast.tar.gz", "-C", HOME] in a
    assert ["chown", "-R", "racecast:rc", HOME] in a
    assert a[-1] == ["install", "-d", "-o", "racecast", "-g", "rc", "-m", "0755", HOME + "/.config",
                     HOME + "/.config/obs-studio", HOME + "/.config/obs-studio/plugins"]
    code, _, err = run_main(prov.step_racecast, FakeHost(homes={}), "racecast", {})
    assert code == 1 and err == "provision.py: no home directory for user racecast\n"


def t_prov_install_steps_tolerate_failures():
    h = FakeHost(which={"racecast"}, rules=[("install-tools", 3, ""), ("install-apps", 4, ""),
                                            (("getent",), 0, "racecast:x:1:1::/home/racecast:/bin/bash\n")])
    code, out, _ = run_main(prov.step_install_tools, h, "racecast")
    assert code == 0 and "install-tools reported issues: see the verification block below" in out
    code, out, _ = run_main(prov.step_install_apps, h, "racecast")
    assert code == 0 and "install-apps reported issues" in out
    assert ["sudo", "-u", "racecast", "-H", "racecast", "install-apps", "--yes"] in h.argvs()
    chown = [c for c in h.calls if c[0] == "run" and c[1][0] == "chown"][0]
    assert chown[1][-1] == HOME + "/.config" and chown[3] == DEVNULL


def t_prov_tailscale_branches():
    h = FakeHost(which={"tailscale"}, rules=[(("tailscale", "ip"), 0, "100.64.0.9\n")])
    code, out, _ = run_main(prov.step_tailscale, h, "racecast", {})
    assert "already joined the tailnet (100.64.0.9)" in out
    assert h.argvs()[-1] == ["tailscale", "set", "--operator=racecast"]
    h = FakeHost(rules=[(("tailscale", "status"), 1, ""), ("tailscale up", 1, "")])
    code, out, _ = run_main(prov.step_tailscale, h, "racecast", {"TS_AUTHKEY": "tskey"})
    assert code == 0, "a failed unattended join must still reach the verification block"
    assert ("unattended tailnet join failed. Re-run: sudo tailscale up --ssh --authkey <key> "
            "--hostname racecast-box") in out
    assert ["sh", "-c", 'exec tailscale up --ssh --authkey "$TS_AUTHKEY" --hostname racecast-box'] in h.argvs()
    assert not any("tskey" in " ".join(a) for a in h.argvs()), "the auth key never appears in an argv"
    h = FakeHost(tty=True, rules=[(("tailscale", "status"), 1, ""), (("tailscale", "up"), 1, "")])
    code, out, _ = run_main(prov.step_tailscale, h, "racecast", {})
    assert code == 0 and "tailscale up did not complete." in out
    h = FakeHost(rules=[(("tailscale", "status"), 1, "")])
    code, out, _ = run_main(prov.step_tailscale, h, "racecast", {})
    assert "REQUIRED join not done" in out and "      sudo tailscale up --ssh --hostname racecast-box\n" in out
    assert not any(a[:2] == ["tailscale", "up"] for a in h.argvs())


def t_prov_copies_prepare_event():
    h = FakeHost(files={"/opt/cloud/prepare-event.py": ""}, rules=[(("id", "-gn"), 0, "rc\n")])
    code, out, _ = run_main(prov.step_copy_prepare_event, h, "racecast", "/opt/cloud")
    assert h.argvs()[-1] == ["install", "-m", "0755", "-o", "racecast", "-g", "rc", "/opt/cloud/prepare-event.py",
                             "/home/racecast/prepare-event.py"]
    assert "prepare-event.py -> /home/racecast/prepare-event.py" in out
    code, out, _ = run_main(prov.step_copy_prepare_event, FakeHost(), "racecast", "/opt/cloud")
    assert code == 0 and "prepare-event.py not beside provision.py (startup-script mode?)" in out


def _verify_host(gpu, nvenc=True, missing=()):
    mbin = HOME + "/runtime/bin"
    which = {"ffmpeg", "racecast", "firefox", "rustdesk", "obs", "tailscale"} - set(missing)
    files = {HOME + "/.config/obs-studio/plugins/linux-pipewire-audio/bin/64bit/linux-pipewire-audio.so": "",
             "/usr/lib/obs-plugins/obs-browser.so": "", "/usr/bin/discord": "",
             HOME + "/.config/autostart/light-locker.desktop": ""}
    execs = {f"{mbin}/{t}" for t in ("yt-dlp", "streamlink", "deno")} | {
        HOME + "/.local/bin/racecast-obs-launch", HOME + "/.local/bin/racecast-discord-launch"}
    rules = [(("getent",), 0, "racecast:x:1:1::/home/racecast:/bin/bash\n"),
             (("lspci",), 0, "00:1f.0 3D controller: NVIDIA L4\n" if gpu else "00:03.0 Ethernet\n"),
             (("ldconfig", "-p"), 0, "libnvidia-encode.so.1\n"),
             (("ffmpeg",), 0, " V..... h264_nvenc\n" if nvenc else " V..... libx264\n"),
             (("pgrep",), 1, ""), ("--version", 0, "v1\n"), ("driver_version", 0, "580.1\n")]
    return FakeHost(which=which, files=files, execs=execs, dirs={"/opt/companion"}, rules=rules)


def t_prov_verification_verdicts():
    code, out, _ = run_main(lambda: sys.exit(prov.verify(_verify_host(gpu=False), "racecast")))
    assert code == 0, out
    assert "  \033[1;32mOK\033[0m  yt-dlp: v1 (managed)\n" in out
    assert "  \033[1;32mOK\033[0m  streamlink: v1 (managed venv)\n" in out
    assert "no NVIDIA GPU on this host. GPU stack (driver/NVENC/X-on-GPU) NOT verified." in out
    assert "X server: not running yet." in out, "post-reboot state advises without failing"
    code, out, _ = run_main(lambda: sys.exit(prov.verify(_verify_host(gpu=True, nvenc=False), "racecast")))
    assert code == 1 and "  \033[1;33m!!\033[0m  ffmpeg NVENC encoders: MISSING\n" in out
    assert "nvidia driver: 580.1" in out
    code, out, _ = run_main(lambda: sys.exit(prov.verify(_verify_host(gpu=False, missing=("obs",)), "racecast")))
    assert code == 1 and "obs: MISSING. Re-run install-apps" in out


def t_prov_reboot_only_after_the_first_green_run():
    h = FakeHost(files={prov.PROVISIONED_STAMP: ""})
    code, out, _ = run_main(prov.finish, h, "racecast", 0, {})
    assert code == 0 and ["reboot"] not in h.argvs(), "a startup-script re-run on every boot must not reboot again"
    assert "already provisioned on an earlier run (/var/lib/racecast/provisioned): no reboot" in out
    h = FakeHost()
    code, out, _ = run_main(prov.finish, h, "racecast", 1, {})
    assert code == 1 and ["reboot"] not in h.argvs(), "a red run does not reboot, it would loop as a startup-script"
    assert prov.PROVISIONED_STAMP not in h.files
    assert "not rebooting while a component is missing" in out
    h = FakeHost()
    run_main(prov.finish, h, "racecast", 0, {"PROVISION_REBOOT": "0"})
    assert h.files.get(prov.PROVISIONED_STAMP) == "" and ["reboot"] not in h.argvs()


def t_prov_finish_and_reboot_gate():
    h = FakeHost()
    code, out, _ = run_main(prov.finish, h, "racecast", 0, {})
    assert code == 0 and ("sleep", 10) in h.calls and h.argvs()[-1] == ["reboot"]
    assert h.files[prov.PROVISIONED_STAMP] == "" and ["install", "-d", "-m", "0755", "/var/lib/racecast"] in h.argvs()
    assert "provision.py complete. Box FULLY EQUIPPED" in out
    assert "      Event stack is owned by 'racecast' (its home: /home/racecast).\n" in out
    h = FakeHost()
    code, out, _ = run_main(prov.finish, h, "racecast", 0, {"PROVISION_REBOOT": "0"})
    assert code == 0 and ["reboot"] not in h.argvs()
    assert "PROVISION_REBOOT=0: skipping the reboot." in out
    code, out, _ = run_main(prov.finish, FakeHost(), "racecast", 1, {"PROVISION_REBOOT": "0"})
    assert code == 1 and "finished with MISSING components" in out
    code, _, _ = run_main(prov.finish, FakeHost(rules=[(("reboot",), 5, "")]), "racecast", 0, {"PROVISION_REBOOT": ""})
    assert code == 5, "an empty PROVISION_REBOOT means reboot; a failed reboot exits with its status"


def t_prov_requires_root_and_creates_user():
    code, _, err = run_main(prov.main, {}, FakeHost(rules=[(("id", "-u"), 0, "1000\n")]), "/x")
    assert code == 1 and err == "provision.py must run as root (use: sudo python3 provision.py)\n"
    h = FakeHost(rules=[(("id", "-u", "racecast"), 1, ""), (("getent", "group", "plugdev"), 2, "")])
    code, out, _ = run_main(prov.ensure_user, h, "racecast")
    a = h.argvs()
    assert a[0] == ["id", "-u", "racecast"] and a[1] == ["useradd", "-m", "-s", "/bin/bash", "racecast"]
    assert "created event user racecast (home /home/racecast)" in out
    assert not any(x[:2] == ["getent", "passwd"] for x in a), "no passwd entry is read or printed"
    assert ["usermod", "-aG", "video", "racecast"] in a and ["usermod", "-aG", "plugdev", "racecast"] not in a


def t_prov_apt_step_and_failure():
    saved = os.environ.get("DEBIAN_FRONTEND")
    try:
        h = FakeHost()
        run_main(prov.step_apt, h)
        assert h.argvs()[:2] == [["apt-get", "update"], ["apt-get", "-y", "upgrade"]]
        assert h.argvs()[2] == ["apt-get", "install", "-y", "curl", "python3", "python3-venv", "python3-pip",
                                "ca-certificates", "wget", "gnupg", "pciutils", "mesa-utils", "x11-utils"]
        assert os.environ["DEBIAN_FRONTEND"] == "noninteractive"
        code, _, _ = run_main(prov.step_apt, FakeHost(rules=[(("apt-get", "-y", "upgrade"), 100, "")]))
        assert code == 100
    finally:
        if saved is None:
            os.environ.pop("DEBIAN_FRONTEND", None)
        else:
            os.environ["DEBIAN_FRONTEND"] = saved


def t_scripts_are_python_and_self_contained():
    names = sorted(os.listdir(CLOUD))
    assert not [n for n in names if n.endswith(".sh")], "the cloud tools are Python-only"
    for n in ("provision.py", "prepare-event.py", "provision-iptest.py"):
        with open(os.path.join(CLOUD, n), encoding="utf-8") as f:
            src = f.read()
        assert src.startswith("#!/usr/bin/env python3\n"), n
        for node in ast.walk(ast.parse(src)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for mod in mods:
                assert mod.split(".")[0] in sys.stdlib_module_names, f"{n} imports {mod}, which a bare box lacks"


RUSTDESK_HELPER_EXPECTED = r"""#!/usr/bin/env bash
# Managed by racecast provision.py. Configures RustDesk once the desktop session is up.
set -u
USER_NAME="racecast"
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

OBS_LAUNCH_EXPECTED = r"""#!/bin/sh
# Managed by racecast provision.py. Clear stale OBS crash sentinels so a hard-killed
# session (cloud instance stop/reboot) never triggers OBS's "did not shut down properly /
# Run in Safe Mode?" prompt on this unattended autologin box (Safe Mode also disables
# obs-websocket, which the relay drives).
rm -f "$HOME/.config/obs-studio/.sentinel/"run_* 2>/dev/null || true
exec obs "$@"
"""

DISCORD_LAUNCH_EXPECTED = r"""#!/bin/sh
# Managed by racecast provision.py. Start Discord with the plaintext password store
# instead of the GNOME keyring: a passwordless-autologin account cannot auto-unlock the
# keyring, so the keyring-backed store pops an "Unlock Keyring" dialog on every start.
# --password-store=basic (a Chromium/Electron flag Discord honors) sidesteps it. The box
# is single-user + tailnet-only, so plaintext app-token storage is an acceptable trade
# for zero interactive prompts.
exec discord --password-store=basic "$@"
"""


def t_host_home_lookup_without_passwd_output():
    if os.name == "nt":
        return  # "~user" expansion only means a passwd lookup on the Linux box
    for mod in (prov, ipt):
        assert mod.Host().home("no-such-user-racecast-test") is None, mod.__name__
        assert mod.Host().home("root") not in (None, "~root"), mod.__name__
        assert '"getent", "passwd"' not in _read(mod.__file__), f"{mod.__name__} reads no passwd entries"


def t_prov_payloads_match_golden():
    assert prov.rustdesk_helper("racecast") == RUSTDESK_HELPER_EXPECTED
    assert prov.OBS_LAUNCH == OBS_LAUNCH_EXPECTED
    assert prov.DISCORD_LAUNCH == DISCORD_LAUNCH_EXPECTED


def t_prov_main_runs_the_steps_in_order():
    h = FakeHost(files={"/opt/cloud/prepare-event.py": ""}, which={"racecast", "tailscale"}, rules=[
        (("id", "-u", "racecast"), 0, "1001\n"), (("id", "-u"), 0, "0\n"), (("id", "-gn"), 0, "racecast\n"),
        (("lspci",), 0, "00:03.0 Ethernet\n"), (("tailscale", "status"), 1, "")])
    code, out, _ = run_main(prov.main, {"TS_AUTHKEY": "k", "PROVISION_REBOOT": "0"}, h, "/opt/cloud")
    headers = [line.split("==>\033[0m ", 1)[1] for line in out.split("\n") if "==>\033[0m " in line]
    steps = [x.split()[0] for x in headers if x[:1].isdigit()]
    assert steps == [f"{i}/10" for i in range(1, 11)], steps
    order = [next(i for i, x in enumerate(headers) if x.startswith(p)) for p in
             ("10/10", "copying prepare-event.py", "verification:", "PROVISION_REBOOT=0")]
    assert order == sorted(order), headers

    def at(match):
        return next(i for i, c in enumerate(h.calls) if match(c))
    sudoers = at(lambda c: c == ("write", "/etc/sudoers.d/90-racecast"))
    tools = at(lambda c: c[0] == "run" and c[1][-1:] == ["install-tools"])
    join = at(lambda c: c[0] == "run" and c[1][:2] == ["sh", "-c"] and "tailscale up" in c[1][2])
    copy = at(lambda c: c[0] == "run" and c[1][-1:] == ["/home/racecast/prepare-event.py"])
    check = at(lambda c: c[0] == "run" and c[1][:3] == ["dpkg", "-s", "lightdm"])
    assert sudoers < tools < join < copy < check, "sudoers, install-tools, join, copy, verification"
    assert ["reboot"] not in h.argvs()


def t_host_os_errors_map_to_126():
    real = subprocess.run

    def denied(*a, **kw):
        raise PermissionError(13, "Permission denied")
    subprocess.run = denied
    try:
        for mod in (aws, gcp, prep, ipt, r505, regions, prov):
            host = mod.Host()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc_run = host.run(["tool"])
                rc_capture = host.capture(["tool"])[0]
            assert (rc_run, rc_capture) == (126, 126), mod.__name__
            assert err.getvalue() == "tool: Permission denied\ntool: Permission denied\n", (mod.__name__, err.getvalue())
    finally:
        subprocess.run = real


def t_docstrings_describe_the_code_not_bash():
    for mod in (aws, gcp, prep, ipt, r505, regions, prov):
        with open(mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node) or ""
                assert not re.search(r"\bbash\b|pipefail|set -e|\$\(|cut -d", doc), f"{mod.__name__}.{node.name}: {doc}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
