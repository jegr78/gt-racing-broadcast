#!/usr/bin/env python3
"""Shared helpers for the racecast installer verbs (install-tools, install-apps).
Loaded by both via importlib from the sibling path, which works in repo mode, in
the test loaders and in the frozen binary, where scripts ship under _MEIPASS."""
import os, shutil, subprocess
import http_util

from services import external_tool_env  # de-PyInstaller the env for spawned installers

# Standard Homebrew locations: Apple Silicon, then Intel. A fresh bootstrap is
# NOT on the current process PATH (shellenv only runs in new shells), so brew
# must be invoked via the absolute path find_brew() returns.
BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
BREW_INSTALLER = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"


def _fetch(url, timeout):
    """GET `url` over cert-verified HTTPS with a real User-Agent, returning the body."""
    return http_util.get_bytes(url, timeout=timeout)


def confirmed(answer):
    """True iff the operator's reply means yes."""
    return answer.strip().lower().startswith("y")


# winget result codes that mean the package is already there, not failures:
# 0x8A15002B UPDATE_NOT_APPLICABLE (installed, no newer version in the source),
# 0x8A150061 PACKAGE_ALREADY_INSTALLED. subprocess reports them as unsigned
# DWORDs and PowerShell shows them signed, so normalize via the 32-bit mask.
WINGET_ALREADY_INSTALLED = (0x8A15002B, 0x8A150061)


def install_exit_ok(manager, code):
    """True iff this install exit code means the package is (already) installed."""
    if code == 0:
        return True
    return manager == "winget" and (code & 0xFFFFFFFF) in WINGET_ALREADY_INSTALLED


def find_brew(which=shutil.which, exists=os.path.exists):
    """Absolute brew invocation path, or None. PATH first, then the standard
    install locations, which covers a fresh bootstrap and an unconfigured PATH."""
    hit = which("brew")
    if hit:
        return hit
    for path in BREW_PATHS:
        if exists(path):
            return path
    return None


def brew_installed_casks(brew_path, run=None):
    """Set of cask tokens Homebrew actually tracks (`brew list --cask -1`), or
    None when the probe could not run. install-apps uses this to skip
    `brew upgrade --cask` on apps present on disk but installed OUTSIDE Homebrew,
    where brew errors 'Cask <x> is not installed' and fails the whole upgrade
    batch (#92). None means 'cannot tell', so the caller keeps its best-effort
    behavior rather than wrongly skipping everything."""
    run = subprocess.run if run is None else run
    try:
        out = run([brew_path, "list", "--cask", "-1"],
                  capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()}


def run_remote_script(url, runner):
    """Download url to a temp file over cert-verified HTTPS and run it visibly.
    No shell pipes: the operator saw the URL and confirmed beforehand."""
    import tempfile
    print("Downloading:", url)
    body = _fetch(url, timeout=30)
    # delete=False: the file must outlive the handle so the runner can read it.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sh") as tmp:
        tmp.write(body)
    try:
        cmd = runner + [tmp.name]
        print("Running:", " ".join(cmd))
        # A frozen binary's _MEIPASS must not leak onto the spawned script's
        # LD_LIBRARY_PATH: tailscale's install.sh runs curl, which would else load
        # our bundled libssl and die with "OPENSSL_x.y.z not found". See services.py.
        return subprocess.call(cmd, env=external_tool_env())
    finally:
        os.unlink(tmp.name)


def install_remote_deb(url):
    """Download a vendor .deb over cert-verified HTTPS to a temp file and install
    it visibly with apt-get. No shell pipes: the operator saw the URL and confirmed
    beforehand. World-readable so apt's sandboxed fetcher can read it."""
    import tempfile
    print("Downloading:", url)
    body = _fetch(url, timeout=60)
    # delete=False: the file must outlive the handle so apt-get can read it.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".deb") as tmp:
        tmp.write(body)
    try:
        os.chmod(tmp.name, 0o644)
        cmd = ["sudo", "apt-get", "install", "-y", tmp.name]
        print("Running:", " ".join(cmd))
        # sudo resets LD_LIBRARY_PATH itself, but pass the de-PyInstaller'd env too
        # for consistency with run_remote_script (harmless under sudo).
        return subprocess.call(cmd, env=external_tool_env())
    finally:
        os.unlink(tmp.name)


def bootstrap_brew(assume_yes, input_fn=input, run=None, find=None):
    """Offer the official brew.sh installer on macOS. Returns the absolute brew
    path on success, None if declined or failed. The installer runs as the current
    user, prompts for sudo itself, and may download the Xcode Command Line Tools,
    a one-time setup that can take a while."""
    run = run_remote_script if run is None else run
    find = find_brew if find is None else find
    print("Homebrew is required but not installed. Official installer:")
    print(" ", BREW_INSTALLER)
    print("  (runs as your user, asks for sudo + RETURN; may download the")
    print("   Xcode Command Line Tools, a one-time setup that can take a while)")
    if not assume_yes and not confirmed(input_fn("Bootstrap Homebrew now? [y/N] ")):
        print("aborted.")
        return None
    if run(BREW_INSTALLER, ["/bin/bash"]) != 0:
        return None
    return find()
