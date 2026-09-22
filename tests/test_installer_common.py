#!/usr/bin/env python3
"""Stdlib checks for shared installer helpers. Run: python3 tests/test_installer_common.py"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# installer_common imports its sibling `services` (external_tool_env); in
# production scripts/ is always on sys.path, so mirror that for the loader.
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util
spec = importlib.util.spec_from_file_location(
    "installer_common", os.path.join(ROOT, "src", "scripts", "installer_common.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_confirmed_parsing():
    assert m.confirmed("y") and m.confirmed("Y") and m.confirmed("yes")
    assert not m.confirmed("") and not m.confirmed("n") and not m.confirmed("nein")


def t_find_brew_prefers_path():
    assert m.find_brew(which=lambda n: "/usr/local/bin/brew",
                       exists=lambda p: True) == "/usr/local/bin/brew"


def t_find_brew_standard_locations():
    arm = "/opt/homebrew/bin/brew"
    assert m.find_brew(which=lambda n: None, exists=lambda p: p == arm) == arm
    intel = "/usr/local/bin/brew"
    assert m.find_brew(which=lambda n: None, exists=lambda p: p == intel) == intel
    assert m.find_brew(which=lambda n: None, exists=lambda p: False) is None


def t_install_exit_ok_winget_already_installed():
    assert m.install_exit_ok("winget", 0)
    assert m.install_exit_ok("winget", 0x8A15002B)    # UPDATE_NOT_APPLICABLE (unsigned)
    assert m.install_exit_ok("winget", -1978335189)   # same code, signed 32-bit view
    assert m.install_exit_ok("winget", 0x8A150061)    # PACKAGE_ALREADY_INSTALLED
    assert not m.install_exit_ok("winget", 0x8A150011)  # a real installer failure
    assert not m.install_exit_ok("winget", 1)
    assert m.install_exit_ok("brew", 0)
    assert not m.install_exit_ok("brew", 1)           # only winget has "already" codes


def t_bootstrap_declined_runs_nothing():
    calls = []
    out = m.bootstrap_brew(False, input_fn=lambda prompt: "n",
                           run=lambda url, runner: calls.append(url) or 0,
                           find=lambda: "/opt/homebrew/bin/brew")
    assert out is None and calls == []


def t_bootstrap_yes_runs_and_relocates():
    calls = []
    out = m.bootstrap_brew(True, input_fn=lambda prompt: "n",
                           run=lambda url, runner: calls.append((url, runner)) or 0,
                           find=lambda: "/opt/homebrew/bin/brew")
    assert out == "/opt/homebrew/bin/brew"
    assert calls == [(m.BREW_INSTALLER, ["/bin/bash"])]


def t_bootstrap_failed_install_returns_none():
    out = m.bootstrap_brew(True, input_fn=lambda prompt: "y",
                           run=lambda url, runner: 1,
                           find=lambda: "/opt/homebrew/bin/brew")
    assert out is None


class _Run:
    """Minimal subprocess.run() result stand-in for the brew-list probe."""
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def t_brew_installed_casks_parses_list():
    # `brew list --cask -1` prints one token per line; blank lines are ignored.
    out = _Run(0, "obs\ncompanion\n\ntailscale-app\n")
    casks = m.brew_installed_casks("/opt/homebrew/bin/brew", run=lambda *a, **k: out)
    assert casks == {"obs", "companion", "tailscale-app"}


def t_brew_installed_casks_nonzero_returns_none():
    # A non-zero exit means we cannot trust the list -> None (caller keeps the
    # old best-effort behavior rather than wrongly skipping every upgrade).
    assert m.brew_installed_casks("brew", run=lambda *a, **k: _Run(1, "")) is None


def t_brew_installed_casks_probe_error_returns_none():
    def boom(*a, **k):
        raise OSError("no brew")
    assert m.brew_installed_casks("brew", run=boom) is None


class _FakeResp:
    def __init__(self, body=b"x"):
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


def _capture_request(fn_name, *args):
    """Run installer_common.<fn_name> with urlopen + subprocess.call stubbed,
    returning (return_code, captured Request, captured argv, captured call kwargs)."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp(b"vendor-bytes")

    orig_open, orig_call = http_util.urlopen, m.subprocess.call
    http_util.urlopen = fake_urlopen
    m.subprocess.call = lambda cmd, **kw: (captured.update(cmd=cmd, call_kw=kw), 0)[1]
    try:
        rc = getattr(m, fn_name)(*args)
    finally:
        http_util.urlopen, m.subprocess.call = orig_open, orig_call
    return rc, captured.get("req"), captured.get("cmd"), captured.get("call_kw", {})


def t_install_remote_deb_sends_user_agent():
    # Discord's /api/download returns HTTP 403 to the default python-urllib
    # User-Agent; the vendor .deb fetch must carry a real one (any non-urllib UA
    # works).
    rc, req, cmd, call_kw = _capture_request(
        "install_remote_deb", "https://discord.com/api/download?platform=linux&format=deb")
    assert rc == 0
    ua = req.get_header("User-agent")               # None if header absent
    assert ua and "urllib" not in ua.lower()
    assert cmd[:3] == ["sudo", "apt-get", "install"]
    assert "env" in call_kw                          # de-PyInstaller'd env passed through


def t_run_remote_script_passes_clean_env_and_user_agent():
    # The spawned script (e.g. tailscale's install.sh -> curl) must NOT inherit a
    # frozen binary's _MEIPASS on LD_LIBRARY_PATH (curl else loads our bundled
    # libssl: "OPENSSL_x.y.z not found"). It also fetches with a real UA.
    rc, req, cmd, call_kw = _capture_request(
        "run_remote_script", "https://tailscale.com/install.sh", ["sh"])
    assert rc == 0
    ua = req.get_header("User-agent")
    assert ua and "urllib" not in ua.lower()
    assert cmd[0] == "sh"
    assert "env" in call_kw                          # external_tool_env() is wired in


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
