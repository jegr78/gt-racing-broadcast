#!/usr/bin/env python3
"""Stdlib checks for the obs-pipewire-audio-capture (Linux) install helpers.
Run: python3 tests/test_obs_pipewire_linux.py

Covers the PURE logic + the SHA-verified download/extract: plugin paths, arch
gating (x86_64-only prebuilt), flatpak detection, install-missing detection, the
pinned asset URL/hash, the install-apps pointer, and a real tar extract into a
temp OBS plugins dir. Mirrors obs_browser_linux's test shape."""
import importlib.util, os, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
spec = importlib.util.spec_from_file_location(
    "obs_pipewire_linux", os.path.join(ROOT, "src", "scripts", "obs_pipewire_linux.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# Pure path helpers.
def t_user_plugins_dir_posix():
    # A fixed POSIX path: explicit '/', never os.path.join (Windows CI runner).
    assert m.user_plugins_dir("/home/op") == "/home/op/.config/obs-studio/plugins"
    assert m.user_plugins_dir("/home/op/") == "/home/op/.config/obs-studio/plugins"


def t_plugin_so_path_matches_preflight_candidate():
    # must equal preflight.pipewire_audio_candidates()[0] (the per-user layout).
    assert m.plugin_so_path("/home/op") == (
        "/home/op/.config/obs-studio/plugins/linux-pipewire-audio/bin/64bit/"
        "linux-pipewire-audio.so")


def t_plugin_installed_detects_so():
    present = m.plugin_installed("/h", exists=lambda p: p.endswith("linux-pipewire-audio.so"))
    assert present is True
    assert m.plugin_installed("/h", exists=lambda p: False) is False


def t_is_prebuilt_arch_x86_64_only():
    # the release ships a 64bit (x86_64) .so only; aarch64 has no prebuilt.
    assert m.is_prebuilt_arch("x86_64") is True
    assert m.is_prebuilt_arch("amd64") is True
    assert m.is_prebuilt_arch("aarch64") is False
    assert m.is_prebuilt_arch("arm64") is False
    assert m.is_prebuilt_arch("") is False


def t_is_flatpak_obs_detects_var_app():
    # flatpak OBS keeps its tree under ~/.var/app/com.obsproject.Studio.
    assert m.is_flatpak_obs("/h", exists=lambda p: "com.obsproject.Studio" in p) is True
    assert m.is_flatpak_obs("/h", exists=lambda p: False) is False


# The pinned asset.
def t_download_url_is_github_non_flatpak():
    url = m.download_url()
    p = urlparse(url)
    assert p.scheme == "https" and p.netloc == "github.com"
    assert url.endswith(f"/linux-pipewire-audio-{m.PLUGIN_VERSION}.tar.gz")
    assert "flatpak" not in url         # the non-flatpak asset (Flathub covers flatpak)
    assert len(m.PLUGIN_SHA256) == 64


# SHA-verified download and extract.
def _fake_tarball():
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"\x7fELF fake so"
        info = tarfile.TarInfo("linux-pipewire-audio/bin/64bit/linux-pipewire-audio.so")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        loc = b"[Text]\n"
        li = tarfile.TarInfo("linux-pipewire-audio/data/locale/en-US.ini")
        li.size = len(loc)
        tf.addfile(li, io.BytesIO(loc))
    return buf.getvalue()


def t_install_pipewire_audio_verifies_and_extracts():
    import hashlib, tempfile
    blob = _fake_tarball()
    digest = hashlib.sha256(blob).hexdigest()
    with tempfile.TemporaryDirectory() as home:
        so = m.install_pipewire_audio(home, opener=lambda url: blob, sha256=digest)
        assert so == m.plugin_so_path(home)
        assert os.path.isfile(so)
        assert os.path.isfile(home + "/.config/obs-studio/plugins/"
                              "linux-pipewire-audio/data/locale/en-US.ini")


def t_install_pipewire_audio_rejects_bad_checksum():
    import tempfile
    blob = _fake_tarball()
    with tempfile.TemporaryDirectory() as home:
        try:
            m.install_pipewire_audio(home, opener=lambda url: blob, sha256="00" * 32)
            raise AssertionError("expected a checksum mismatch to raise")
        except RuntimeError as exc:
            assert "checksum" in str(exc).lower()


def t_install_pipewire_audio_rejects_path_traversal():
    import io, tarfile, tempfile, hashlib
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        evil = tarfile.TarInfo("../evil.so")
        evil.size = 1
        tf.addfile(evil, io.BytesIO(b"x"))
    blob = buf.getvalue()
    with tempfile.TemporaryDirectory() as home:
        try:
            m.install_pipewire_audio(home, opener=lambda url: blob,
                                     sha256=hashlib.sha256(blob).hexdigest())
            raise AssertionError("expected path traversal to be rejected")
        except RuntimeError as exc:
            assert "unsafe" in str(exc).lower() or "traversal" in str(exc).lower()


# The install-apps decision hint.
def t_install_hint_paths():
    # nothing to say when OBS is absent or the plugin is already there.
    assert m.install_hint("x86_64", obs_present=False, plugin_present=False,
                           is_flatpak=False) is None
    assert m.install_hint("x86_64", obs_present=True, plugin_present=True,
                           is_flatpak=False) is None
    # flatpak OBS -> point at Flathub, do not offer the tarball.
    fp = m.install_hint("x86_64", obs_present=True, plugin_present=False, is_flatpak=True)
    assert fp and "Flathub" in fp
    # aarch64 -> no prebuilt .so.
    arm = m.install_hint("aarch64", obs_present=True, plugin_present=False, is_flatpak=False)
    assert arm and ("aarch64" in arm or "no prebuilt" in arm.lower())


def t_plugin_present_sees_a_system_wide_install():
    # On Arch the plugin comes from the AUR and lands system-wide, so checking
    # only the per-user dir downloads a SECOND copy into ~/.config and leaves OBS
    # with two builds of it. plugin_present() delegates to preflight's candidate
    # list rather than keeping a second copy in sync.
    home = "/home/u"
    assert m.plugin_present(home, exists=lambda p: p == m.plugin_so_path(home)) is True
    sys_so = "/usr/lib/obs-plugins/" + m.PLUGIN_SO
    assert m.plugin_present(home, exists=lambda p: p == sys_so) is True
    multiarch_so = "/usr/lib/x86_64-linux-gnu/obs-plugins/" + m.PLUGIN_SO
    assert m.plugin_present(home, "x86_64", exists=lambda p: p == multiarch_so) is True
    assert m.plugin_present(home, exists=lambda p: False) is False
    assert m.plugin_installed(home, exists=lambda p: p == sys_so) is False, \
        "the narrow per-user check stays available and unchanged"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
