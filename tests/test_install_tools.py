#!/usr/bin/env python3
"""Stdlib checks for install_tools decision helpers. Run: python3 tests/test_install_tools.py"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
spec = importlib.util.spec_from_file_location(
    "install_tools", os.path.join(ROOT, "src", "scripts", "install_tools.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_pick_manager_per_platform():
    have_all = lambda name: "/usr/bin/" + name
    assert m.pick_manager("win32", which=have_all) == "winget"
    assert m.pick_manager("darwin", which=have_all) == "brew"
    assert m.pick_manager("linux", which=have_all) == "apt"
    assert m.pick_manager("win32", which=lambda n: None) is None


def t_missing_tools():
    assert m.missing_tools(which=lambda n: None) == list(m.TOOLS)
    assert m.missing_tools(which=lambda n: "/bin/" + n) == []
    only_ffmpeg = lambda n: "/bin/ffmpeg" if n == "ffmpeg" else None
    assert m.missing_tools(which=only_ffmpeg) == ["yt-dlp", "streamlink", "deno"]


def t_install_commands_winget_one_per_tool():
    cmds = m.install_commands("winget", ["yt-dlp", "deno"])
    assert len(cmds) == 2
    assert cmds[0][:3] == ["winget", "install", "--id"]
    assert cmds[0][3] == "yt-dlp.yt-dlp" and cmds[1][3] == "DenoLand.Deno"


def t_install_commands_brew_single_batch():
    assert m.install_commands("brew", ["ffmpeg", "deno"]) == \
        [["brew", "install", "ffmpeg", "deno"]]
    assert m.install_commands("brew", []) == []


def t_install_commands_apt_updates_then_skips_managed():
    # apt handles only ffmpeg. yt-dlp, deno and streamlink are managed installs.
    cmds = m.install_commands("apt", ["yt-dlp", "streamlink", "ffmpeg", "deno"])
    assert cmds == [["apt-get", "update"], ["apt-get", "install", "-y", "ffmpeg"]]
    assert m.install_commands("apt", ["yt-dlp"]) == []
    assert m.install_commands("apt", ["deno"]) == []
    assert m.install_commands("apt", ["streamlink"]) == []
    assert "yt-dlp" not in m.APT_PACKAGES
    assert "streamlink" not in m.APT_PACKAGES


def t_streamlink_managed_on_linux_only():
    assert m.streamlink_needs_managed_install("apt") is True, \
        "apt's streamlink predates 8.2.0, so Linux needs a managed venv"
    assert m.streamlink_needs_managed_install("brew") is False    # brew ships 8.x
    assert m.streamlink_needs_managed_install("winget") is False  # winget ships 8.x
    assert m.STREAMLINK_VERSION >= "8.2.0", \
        "the pinned streamlink must stay at or above preflight's 8.2.0 floor"
    assert m.STREAMLINK_SPEC == "streamlink==" + m.STREAMLINK_VERSION


def t_streamlink_venv_dir_under_runtime():
    assert m.streamlink_venv_dir("/r") == os.path.join("/r", "streamlink-venv")


def t_install_streamlink_venv_orchestration():
    import tempfile
    calls = []
    links = []
    def rec_symlink(src, dst):
        links.append((src, dst))
    with tempfile.TemporaryDirectory() as td:
        managed = os.path.join(td, "bin")
        venv = os.path.join(td, "streamlink-venv")
        link = m.install_streamlink_venv(
            managed, venv, python="/usr/bin/python3",
            run=calls.append, symlink=rec_symlink)
    # System python builds the venv; the venv's own python installs the pin.
    assert calls[0] == ["/usr/bin/python3", "-m", "venv", venv]
    venv_py = os.path.join(venv, "bin", "python")
    assert calls[1] == [venv_py, "-m", "pip", "install", "--upgrade", m.STREAMLINK_SPEC]
    assert links == [(os.path.join(venv, "bin", "streamlink"),
                      os.path.join(managed, "streamlink"))]
    assert link == os.path.join(managed, "streamlink")


def t_install_streamlink_venv_needs_python():
    import tempfile
    orig = m.system_python
    m.system_python = lambda: None   # a box with no python3 or python3-venv
    try:
        with tempfile.TemporaryDirectory() as td:
            try:
                m.install_streamlink_venv(os.path.join(td, "bin"),
                                          os.path.join(td, "v"), python=None,
                                          run=lambda a: None, symlink=lambda s, d: None)
                raise AssertionError("expected RuntimeError when no python is found")
            except RuntimeError as exc:
                assert "python" in str(exc).lower()
    finally:
        m.system_python = orig


def t_pick_manager_pacman_on_arch():
    # Arch has no apt-get, so without pacman support install-tools finds none. (#560)
    only = lambda have: (lambda n: "/usr/bin/" + n if n == have else None)
    assert m.pick_manager("linux", which=only("pacman")) == "pacman"
    assert m.pick_manager("linux", which=only("apt-get")) == "apt"
    assert m.pick_manager("linux", which=lambda n: None) is None
    assert m.pick_manager("linux", which=lambda n: "/usr/bin/" + n) == "apt", \
        "a box carrying both managers keeps its existing apt path"


def t_install_commands_pacman_takes_all_four_from_the_repo():
    # Arch's repo versions are current enough for every tool, so nothing needs a
    # managed install: one command, all four packages.
    cmds = m.install_commands("pacman", ["yt-dlp", "streamlink", "ffmpeg", "deno"],
                              sudo=True)
    assert cmds == [["sudo", "pacman", "-S", "--needed", "--noconfirm",
                     "yt-dlp", "streamlink", "ffmpeg", "deno"]]
    assert all(t in m.PACMAN_PACKAGES for t in m.TOOLS)
    assert m.install_commands("pacman", []) == []
    # No `-Sy`: refreshing the db without upgrading it is Arch's partial-upgrade
    # trap, and install-tools must never put a machine in that state.
    assert not any("-Sy" in arg for cmd in cmds for arg in cmd)


def t_install_commands_pacman_sudo_prefix():
    assert m.install_commands("pacman", ["ffmpeg"], sudo=False)[0][0] == "pacman", \
        "pacman needs root and does not prompt for it, same as apt"
    assert m.install_commands("pacman", ["ffmpeg"], sudo=True)[0][0] == "sudo"


def t_update_commands_pacman_refuses_partial_upgrade():
    # Upgrading single packages on a rolling release is the partial upgrade, so
    # --update emits no command; main() points at `pacman -Syu`.
    assert m.update_commands("pacman", ["ffmpeg", "deno"]) == []


def t_streamlink_not_managed_on_pacman():
    assert m.streamlink_needs_managed_install("pacman") is False, \
        "arch ships streamlink 8.x, so the apt-only venv workaround stays off"
    assert m.streamlink_needs_managed_install("apt") is True


def t_manual_guide_mentions_deno_on_linux():
    assert "deno" in m.manual_guide("linux")
    assert "brew install" in m.manual_guide("darwin")
    assert "winget" in m.manual_guide("win32")


def t_manual_guide_pacman_variant():
    guide = m.manual_guide("linux", manager="pacman")
    assert "pacman -S" in guide
    assert "apt-get" not in guide          # the apt text is actively wrong on Arch
    for tool in m.TOOLS:
        assert tool in guide
    assert "apt-get" in m.manual_guide("linux"), \
        "the manual guide is unchanged for everyone else"


def t_windows_fresh_path_joins_registry_values():
    vals = ["C:\\sys\\bin", "C:\\user\\bin"]
    assert m.windows_fresh_path(read_values=lambda: vals) == os.pathsep.join(vals)
    assert m.windows_fresh_path(read_values=lambda: []) is None
    assert m.windows_fresh_path(read_values=lambda: ["", ""]) is None


def t_install_commands_brew_absolute_path():
    assert m.install_commands("brew", ["ffmpeg", "deno"],
                              brew_path="/opt/homebrew/bin/brew") == \
        [["/opt/homebrew/bin/brew", "install", "ffmpeg", "deno"]]


def t_update_commands_winget_one_per_tool():
    cmds = m.update_commands("winget", ["yt-dlp", "streamlink"])
    assert len(cmds) == 2
    assert cmds[0][:3] == ["winget", "upgrade", "--id"]
    assert cmds[0][3] == "yt-dlp.yt-dlp"
    assert "--accept-package-agreements" in cmds[0]


def t_update_commands_brew_single_batch():
    assert m.update_commands("brew", ["ffmpeg", "deno"]) == \
        [["brew", "upgrade", "ffmpeg", "deno"]]
    assert m.update_commands("brew", [], brew_path="/opt/homebrew/bin/brew") == []
    assert m.update_commands("brew", ["ffmpeg"],
                             brew_path="/opt/homebrew/bin/brew")[0][0] == \
        "/opt/homebrew/bin/brew"


def t_update_commands_apt_only_upgrade_skips_managed():
    cmds = m.update_commands("apt", ["ffmpeg", "streamlink", "deno", "yt-dlp"])
    assert cmds == [["apt-get", "update"],
                    ["apt-get", "install", "-y", "--only-upgrade", "ffmpeg"]]
    assert m.update_commands("apt", ["deno"]) == []
    assert m.update_commands("apt", ["yt-dlp"]) == []
    assert m.update_commands("apt", ["streamlink"]) == []


def t_speedtest_install_commands_winget_only():
    # Windows installs via winget; mac/Linux are a direct download, not a command.
    win = m.speedtest_install_commands("winget")
    assert win == [["winget", "install", "--id", "Ookla.Speedtest.CLI", "-e",
                    "--accept-package-agreements", "--accept-source-agreements"]]
    assert m.speedtest_install_commands("brew") == []
    assert m.speedtest_install_commands("apt") == []


def t_speedtest_update_commands_winget_only():
    assert m.speedtest_update_commands("winget")[0][:3] == ["winget", "upgrade", "--id"]
    assert m.speedtest_update_commands("brew") == []
    assert m.speedtest_update_commands("apt") == []


def t_speedtest_asset_tag_per_os_arch():
    assert m.speedtest_asset_tag("darwin", "arm64") == "macosx-universal"
    assert m.speedtest_asset_tag("darwin", "x86_64") == "macosx-universal"
    assert m.speedtest_asset_tag("linux", "x86_64") == "linux-x86_64"
    assert m.speedtest_asset_tag("linux", "amd64") == "linux-x86_64"
    assert m.speedtest_asset_tag("linux", "aarch64") == "linux-aarch64"
    assert m.speedtest_asset_tag("linux", "arm64") == "linux-aarch64"
    assert m.speedtest_asset_tag("win32", "AMD64") is None     # winget handles Windows
    assert m.speedtest_asset_tag("linux", "ppc64") is None     # unsupported arch


def t_speedtest_download_url():
    url = m.speedtest_download_url("linux-x86_64")
    assert url == ("https://install.speedtest.net/app/cli/"
                   "ookla-speedtest-1.2.0-linux-x86_64.tgz")


def _fake_tgz(binary_bytes=b"#!/bin/echo speedtest\n"):
    """Build an in-memory .tgz holding a `speedtest` member (+ a noise file)."""
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("speedtest", binary_bytes), ("speedtest.md", b"# doc\n")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 0   # deterministic
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def t_install_speedtest_binary_verifies_and_extracts(tmp=None):
    import hashlib, tempfile
    blob = _fake_tgz()
    sha = hashlib.sha256(blob).hexdigest()
    d = tempfile.mkdtemp()
    path = m.install_speedtest_binary(
        d, "linux-x86_64", opener=lambda url: blob, downloads={"linux-x86_64": sha})
    assert path == os.path.join(d, "speedtest")
    with open(path, "rb") as fh:
        assert fh.read() == b"#!/bin/echo speedtest\n"
    if os.name != "nt":                          # the +x bit is POSIX-only
        import stat
        assert os.stat(path).st_mode & stat.S_IXUSR


def t_install_speedtest_binary_rejects_bad_checksum():
    import tempfile
    blob = _fake_tgz()
    try:
        m.install_speedtest_binary(
            tempfile.mkdtemp(), "linux-x86_64",
            opener=lambda url: blob, downloads={"linux-x86_64": "deadbeef"})
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
        return
    raise AssertionError("expected a checksum-mismatch RuntimeError")


def t_manual_guide_mentions_speedtest():
    assert "Ookla.Speedtest.CLI" in m.manual_guide("win32")
    assert "speedtest.net/apps/cli" in m.manual_guide("darwin")
    assert "speedtest.net/apps/cli" in m.manual_guide("linux")
    assert "teamookla" not in m.manual_guide("darwin")   # no longer the brew-tap path


def t_install_commands_apt_sudo_prefix():
    # apt updates then installs, and both get the sudo prefix. ffmpeg is the only
    # apt-managed tool left.
    assert m.install_commands("apt", ["ffmpeg"]) == \
        [["apt-get", "update"], ["apt-get", "install", "-y", "ffmpeg"]]
    assert m.install_commands("apt", ["streamlink", "ffmpeg"], sudo=True) == \
        [["sudo", "apt-get", "update"],
         ["sudo", "apt-get", "install", "-y", "ffmpeg"]]
    assert m.install_commands("brew", ["ffmpeg"], sudo=True) == [["brew", "install", "ffmpeg"]]
    assert m.install_commands("winget", ["deno"], sudo=True)[0][0] == "winget"
    assert m.install_commands("apt", ["deno"], sudo=True) == []   # deno has no apt pkg


def t_update_commands_apt_sudo_prefix():
    assert m.update_commands("apt", ["ffmpeg"], sudo=True) == \
        [["sudo", "apt-get", "update"],
         ["sudo", "apt-get", "install", "-y", "--only-upgrade", "ffmpeg"]]
    assert m.update_commands("apt", ["ffmpeg"]) == \
        [["apt-get", "update"],
         ["apt-get", "install", "-y", "--only-upgrade", "ffmpeg"]]


def t_deno_asset_tag_per_os_arch():
    assert m.deno_asset_tag("linux", "x86_64") == "x86_64-unknown-linux-gnu"
    assert m.deno_asset_tag("linux", "amd64") == "x86_64-unknown-linux-gnu"
    assert m.deno_asset_tag("linux", "aarch64") == "aarch64-unknown-linux-gnu"
    assert m.deno_asset_tag("linux", "arm64") == "aarch64-unknown-linux-gnu"
    assert m.deno_asset_tag("darwin", "arm64") is None     # brew handles macOS
    assert m.deno_asset_tag("win32", "AMD64") is None      # winget handles Windows
    assert m.deno_asset_tag("linux", "ppc64") is None      # unsupported arch


def t_deno_download_url():
    url = m.deno_download_url("x86_64-unknown-linux-gnu")
    assert url == ("https://github.com/denoland/deno/releases/download/"
                   f"v{m.DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip")


def _fake_deno_zip(binary_bytes=b"#!/bin/echo deno\n"):
    """Build an in-memory .zip holding a single top-level `deno` member, the
    layout of deno's official linux release archive."""
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("deno", binary_bytes)
    return buf.getvalue()


def t_install_deno_binary_verifies_and_extracts():
    import hashlib, tempfile
    blob = _fake_deno_zip()
    sha = hashlib.sha256(blob).hexdigest()
    d = tempfile.mkdtemp()
    path = m.install_deno_binary(
        d, "x86_64-unknown-linux-gnu", opener=lambda url: blob,
        downloads={"x86_64-unknown-linux-gnu": sha})
    assert path == os.path.join(d, "deno")
    with open(path, "rb") as fh:
        assert fh.read() == b"#!/bin/echo deno\n"
    if os.name != "nt":                          # the +x bit is POSIX-only
        import stat
        assert os.stat(path).st_mode & stat.S_IXUSR


def t_install_deno_binary_rejects_bad_checksum():
    import tempfile
    blob = _fake_deno_zip()
    try:
        m.install_deno_binary(
            tempfile.mkdtemp(), "x86_64-unknown-linux-gnu",
            opener=lambda url: blob, downloads={"x86_64-unknown-linux-gnu": "deadbeef"})
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
        return
    raise AssertionError("expected a checksum-mismatch RuntimeError")


def t_ytdlp_asset_tag_per_os_arch():
    assert m.ytdlp_asset_tag("linux", "x86_64") == "linux"
    assert m.ytdlp_asset_tag("linux", "amd64") == "linux"
    assert m.ytdlp_asset_tag("linux", "aarch64") == "linux_aarch64"
    assert m.ytdlp_asset_tag("linux", "arm64") == "linux_aarch64"
    assert m.ytdlp_asset_tag("darwin", "arm64") is None     # brew ships yt-dlp
    assert m.ytdlp_asset_tag("win32", "AMD64") is None       # winget ships yt-dlp
    assert m.ytdlp_asset_tag("linux", "ppc64") is None       # unsupported arch


def t_ytdlp_download_url():
    url = m.ytdlp_download_url("linux")
    assert url == ("https://github.com/yt-dlp/yt-dlp/releases/download/"
                   f"{m.YTDLP_VERSION}/yt-dlp_linux")
    assert m.ytdlp_download_url("linux_aarch64").endswith("/yt-dlp_linux_aarch64")


def t_install_ytdlp_binary_verifies_and_writes():
    import hashlib, tempfile
    blob = b"#!/usr/bin/env python3\n# yt-dlp standalone\n"
    sha = hashlib.sha256(blob).hexdigest()
    d = tempfile.mkdtemp()
    path = m.install_ytdlp_binary(
        d, "linux", opener=lambda url: blob, downloads={"linux": sha})
    assert path == os.path.join(d, "yt-dlp")
    with open(path, "rb") as fh:
        assert fh.read() == blob
    if os.name != "nt":                          # the +x bit is POSIX-only
        import stat
        assert os.stat(path).st_mode & stat.S_IXUSR


def t_install_ytdlp_binary_rejects_bad_checksum():
    import tempfile
    try:
        m.install_ytdlp_binary(
            tempfile.mkdtemp(), "linux",
            opener=lambda url: b"x", downloads={"linux": "deadbeef"})
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
        return
    raise AssertionError("expected a checksum-mismatch RuntimeError")


def t_glibc_version_parses_glibc_only():
    assert m.glibc_version(("glibc", "2.39")) == (2, 39)
    assert m.glibc_version(("glibc", "2.35")) == (2, 35)
    # Non-glibc or undeterminable means "cannot tell, do not block".
    assert m.glibc_version(("", "")) is None
    assert m.glibc_version(("musl", "1.2.4")) is None
    assert m.glibc_version(("glibc", "")) is None
    assert m.glibc_version(("glibc", "garbage")) is None


def t_min_os_error_below_at_above_floor():
    # Below the deno floor the message names both versions.
    msg = m.min_os_error((2, 31))
    assert msg is not None
    assert "2.31" in msg and "2.35" in msg and "24.04" in msg
    assert m.min_os_error((2, 35)) is None, "at or above the floor there is no error"
    assert m.min_os_error((2, 39)) is None
    assert m.min_os_error(None) is None, "an undeterminable glibc must never block"
    assert m.MIN_GLIBC_TOOLS == (2, 35) and m.MIN_GLIBC_BINARY == (2, 38)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
