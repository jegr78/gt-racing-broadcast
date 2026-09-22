#!/usr/bin/env python3
"""Stdlib checks for the obs-browser (Linux source-build) decision helpers.
Run: python3 tests/test_obs_browser_linux.py

These cover the PURE logic only: arch/version resolution, the pinned CEF spec,
the download URL/hash, the OBS plugin/data dirs, browser-missing detection, the
install-apps pointer, and the BrowserHWAccel global.ini transform. The heavy
orchestration (clone/download/cmake/install) is not exercised in CI."""
import importlib.util, os, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
spec = importlib.util.spec_from_file_location(
    "obs_browser_linux", os.path.join(ROOT, "src", "scripts", "obs_browser_linux.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# Arch normalization.
def t_normalize_arch():
    assert m.normalize_arch("aarch64") == "aarch64"
    assert m.normalize_arch("arm64") == "aarch64"
    assert m.normalize_arch("x86_64") == "x86_64"
    assert m.normalize_arch("amd64") == "x86_64"
    assert m.normalize_arch("armv7l") is None
    assert m.normalize_arch("") is None


def t_arch_triplet():
    assert m.arch_triplet("aarch64") == "aarch64-linux-gnu"
    assert m.arch_triplet("x86_64") == "x86_64-linux-gnu"


# OBS version -> spec key.
def t_obs_version_key():
    assert m.obs_version_key("32.1.0") == "32.1"
    assert m.obs_version_key("32.1.0-0ubuntu3") == "32.1"
    assert m.obs_version_key("32.1") == "32.1"
    assert m.obs_version_key("bogus") is None


def t_resolve_spec_supported_and_not():
    s = m.resolve_spec("32.1.0-0ubuntu3")
    assert s is not None and s["cef_version"] == "6533"
    assert m.resolve_spec("31.0.0") is None   # not in the pinned table


# CEF asset: filename, url and hash.
def t_cef_filename_aarch64():
    s = m.resolve_spec("32.1.0")
    assert m.cef_filename(s, "aarch64") == "cef_binary_6533_linux_aarch64_v6.tar.xz"


def t_cef_filename_x86_64():
    s = m.resolve_spec("32.1.0")
    assert m.cef_filename(s, "x86_64") == "cef_binary_6533_linux_x86_64_v6.tar.xz"


def t_cef_url_is_on_obs_cdn():
    s = m.resolve_spec("32.1.0")
    url = m.cef_url(s, "aarch64")
    assert url == ("https://cdn-fastly.obsproject.com/downloads/"
                   "cef_binary_6533_linux_aarch64_v6.tar.xz")
    assert urlparse(url).scheme == "https"


def t_cef_sha256_per_arch():
    s = m.resolve_spec("32.1.0")
    assert m.cef_sha256(s, "aarch64") == \
        "642514469eaa29a5c887891084d2e73f7dc2d7405f7dfa7726b2dbc24b309999"
    assert m.cef_sha256(s, "x86_64") == \
        "7963335519a19ccdc5233f7334c5ab023026e2f3e9a0cc417007c09d86608146"


def t_obs_browser_commit_pinned():
    s = m.resolve_spec("32.1.0")
    # the obs-browser submodule commit pinned by obs-studio 32.1.0
    assert s["obs_browser_commit"] == "ea04212e4bbadd077f9e6038758c4e4779c24fa3"


# Install locations and detection.
def t_obs_plugins_dir():
    assert m.obs_plugins_dir("aarch64") == "/usr/lib/aarch64-linux-gnu/obs-plugins"
    assert m.obs_plugins_dir("x86_64") == "/usr/lib/x86_64-linux-gnu/obs-plugins"


def t_browser_plugin_installed():
    present = {"/usr/lib/aarch64-linux-gnu/obs-plugins/obs-browser.so"}
    assert m.browser_plugin_installed("/usr/lib/aarch64-linux-gnu/obs-plugins",
                                      exists=present.__contains__) is True
    assert m.browser_plugin_installed("/usr/lib/aarch64-linux-gnu/obs-plugins",
                                      exists=lambda p: False) is False


def t_browser_plugin_installed_uses_forward_slash():
    # The OBS plugins dir is a fixed-OS Linux path, so the lookup must use '/',
    # never os.path.join (which injects '\\' on the Windows CI runner). Capture
    # the exact path queried so this is caught on any OS, not just Windows.
    seen = []
    m.browser_plugin_installed("/usr/lib/aarch64-linux-gnu/obs-plugins",
                               exists=lambda p: seen.append(p) or False)
    assert seen == ["/usr/lib/aarch64-linux-gnu/obs-plugins/obs-browser.so"]
    # a trailing slash on the dir must not double up
    seen.clear()
    m.browser_plugin_installed("/usr/lib/aarch64-linux-gnu/obs-plugins/",
                               exists=lambda p: seen.append(p) or False)
    assert seen == ["/usr/lib/aarch64-linux-gnu/obs-plugins/obs-browser.so"]


# The install-apps pointer.
def t_install_hint_only_when_missing_on_supported_arch():
    # OBS present, browser missing, supported arch -> a pointer is shown
    hint = m.install_hint("aarch64", obs_present=True, browser_present=False)
    assert hint and "racecast obs-browser" in hint
    # browser already there -> nothing
    assert m.install_hint("aarch64", obs_present=True, browser_present=True) is None
    # OBS not installed -> nothing (install OBS first)
    assert m.install_hint("aarch64", obs_present=False, browser_present=False) is None
    # unsupported arch -> nothing
    assert m.install_hint("armv7l", obs_present=True, browser_present=False) is None


def t_pacman_hosts_get_the_package_not_a_source_build():
    # This command is an apt/dpkg path end to end (BUILD_APT_DEPS, dpkg-query).
    # Arch has no dpkg-query, so probing it there reports "OBS Studio not
    # detected" even with OBS running; the real fix there is a package swap.
    note = m.pacman_redirect_note(has_pacman=True, has_apt=False)
    assert note and m.PACMAN_BROWSER_PACKAGE in note
    assert "pacman -S" in note
    assert "apt" not in note.lower().replace("adapt", "")   # no apt advice on Arch
    # Debian/Ubuntu (the intended audience) are untouched, and so is a box that
    # carries both, where apt wins exactly like install_tools.pick_manager.
    assert m.pacman_redirect_note(has_pacman=False, has_apt=True) is None
    assert m.pacman_redirect_note(has_pacman=True, has_apt=True) is None
    assert m.pacman_redirect_note(has_pacman=False, has_apt=False) is None


def t_browser_plugin_found_in_the_non_multiarch_dir_too():
    # Debian puts OBS plugins under a multiarch triplet; Arch uses a plain
    # /usr/lib/obs-plugins, so checking only the Debian path reports "no Browser
    # Source" on a machine that has one.
    dirs = m.obs_plugins_dirs("x86_64")
    assert "/usr/lib/x86_64-linux-gnu/obs-plugins" in dirs
    assert "/usr/lib/obs-plugins" in dirs
    arch_only = lambda p: p == "/usr/lib/obs-plugins/obs-browser.so"
    assert m.browser_plugin_present(dirs, exists=arch_only) is True
    assert m.browser_plugin_present(dirs, exists=lambda p: False) is False


# The PROJECT_ARCH fix is encoded in the CEF wrapper configure.
def t_cef_configure_argv_sets_project_arch_on_aarch64():
    argv = m.cef_configure_argv("/src/cef", "/src/cef/build", "aarch64")
    assert "-DPROJECT_ARCH=arm64" in argv     # CEF cmake reads 'arm64', not 'aarch64'
    assert argv[0] == "cmake"
    # x86_64 must NOT force arm64
    argv64 = m.cef_configure_argv("/src/cef", "/src/cef/build", "x86_64")
    assert not any("PROJECT_ARCH=arm64" in a for a in argv64)


# No-GPU and VM hosts: the BrowserHWAccel guidance and global.ini transform.
def t_hwaccel_note_mentions_setting():
    note = m.browser_hwaccel_note()
    assert "BrowserHWAccel" in note


def t_global_ini_set_hwaccel_existing_key():
    src = "[General]\nName=x\nBrowserHWAccel=true\n"
    out = m.set_browser_hwaccel(src, False)
    assert "BrowserHWAccel=false" in out and "BrowserHWAccel=true" not in out


def t_global_ini_set_hwaccel_adds_when_absent():
    src = "[General]\nName=x\n"
    out = m.set_browser_hwaccel(src, False)
    assert "BrowserHWAccel=false" in out
    # idempotent
    assert m.set_browser_hwaccel(out, False).count("BrowserHWAccel=") == 1


def t_global_ini_set_hwaccel_no_general_section():
    out = m.set_browser_hwaccel("", False)
    assert "[General]" in out and "BrowserHWAccel=false" in out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
