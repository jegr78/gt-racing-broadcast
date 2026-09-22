#!/usr/bin/env python3
"""Stdlib checks for install_apps decision helpers. Run: python3 tests/test_install_apps.py"""
import importlib.util, os, re, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# install_apps -> installer_common imports the sibling `services` (external_tool_env);
# in production scripts/ is always on sys.path, so mirror that for the loader.
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
spec = importlib.util.spec_from_file_location(
    "install_apps", os.path.join(ROOT, "src", "scripts", "install_apps.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_app_ids():
    assert m.WINGET_APP_IDS == {"obs": "OBSProject.OBSStudio",
                                "companion": "Bitfocus.Companion",
                                "tailscale": "Tailscale.Tailscale",
                                "discord": "Discord.Discord"}
    assert m.BREW_CASKS == {"obs": "obs", "companion": "companion",
                            "tailscale": "tailscale-app", "discord": "discord"}


def t_install_commands_winget_one_per_app():
    cmds = m.app_install_commands("winget", ["obs", "tailscale"])
    assert len(cmds) == 2
    assert cmds[0][:3] == ["winget", "install", "--id"]
    assert cmds[0][3] == "OBSProject.OBSStudio" and cmds[1][3] == "Tailscale.Tailscale"
    assert all("--interactive" not in c for c in cmds)


def t_install_commands_companion_is_interactive():
    # Companion's NSIS installer writes NOTHING without admin in silent mode yet
    # exits 0. Only the interactive wizard, with its UAC prompt, works.
    (cmd,) = m.app_install_commands("winget", ["companion"])
    assert cmd[3] == "Bitfocus.Companion" and "--interactive" in cmd


def t_install_commands_brew_single_cask_batch():
    assert m.app_install_commands("brew", ["obs", "tailscale"]) == \
        [["brew", "install", "--cask", "obs", "tailscale-app"]]
    assert m.app_install_commands("brew", []) == []


def t_install_commands_apt_is_manual():
    assert m.app_install_commands("apt", ["obs", "companion", "tailscale"]) == []


def t_app_present_darwin_bundles():
    exists = lambda p: p == "/Applications/OBS.app"
    assert m.app_present("obs", "darwin", exists=exists, which=lambda n: None)
    assert not m.app_present("companion", "darwin", exists=exists, which=lambda n: None)


def t_app_present_windows_paths():
    hit = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
    env = {"ProgramFiles": r"C:\Program Files", "LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
    assert m.app_present("obs", "win32", env=env, exists=lambda p: p == hit,
                         which=lambda n: None)
    # A 32-bit installer registration lands OBS in Program Files (x86); without
    # this candidate install-apps re-"installs" it.
    x86 = r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"
    env_x86 = dict(env, **{"ProgramFiles(x86)": r"C:\Program Files (x86)"})
    assert m.app_present("obs", "win32", env=env_x86, exists=lambda p: p == x86,
                         which=lambda n: None)
    assert not m.app_present("tailscale", "win32", env=env, exists=lambda p: False,
                             which=lambda n: None)


def t_app_present_falls_back_to_which():
    assert m.app_present("tailscale", "linux", exists=lambda p: False,
                         which=lambda n: "/usr/bin/tailscale" if n == "tailscale" else None)


def t_app_present_linux_companion_service():
    # companion-pi installs a systemd service, not a PATH binary
    unit = "/etc/systemd/system/companion.service"
    assert m.app_present("companion", "linux", exists=lambda p: p == unit,
                         which=lambda n: None)
    assert not m.app_present("companion", "linux", exists=lambda p: False,
                             which=lambda n: None)


def t_app_present_discord_paths():
    # Windows: on a Squirrel per-user install, Update.exe is the version-stable path
    env = {"ProgramFiles": r"C:\Program Files",
           "LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
    hit = r"C:\Users\x\AppData\Local\Discord\Update.exe"
    assert m.app_present("discord", "win32", env=env, exists=lambda p: p == hit,
                         which=lambda n: None)
    assert m.app_present("discord", "darwin",
                         exists=lambda p: p == "/Applications/Discord.app",
                         which=lambda n: None)
    assert m.app_present("discord", "linux",
                         exists=lambda p: p == "/usr/bin/discord",
                         which=lambda n: None)
    assert not m.app_present("discord", "linux", exists=lambda p: False,
                             which=lambda n: None)
    # First linux candidate (/usr/share/discord) must also match
    assert m.app_present("discord", "linux",
                         exists=lambda p: p == "/usr/share/discord",
                         which=lambda n: None)


def t_manual_guide_has_urls_per_os():
    # Compare URL HOSTS, not substrings: '"x.com" in guide' would also match an
    # unrelated URL like https://evil.example/?x.com.
    for plat in ("win32", "darwin", "linux"):
        guide = m.apps_manual_guide(plat)
        urls = [u.rstrip("'\"),:") for u in re.findall(r"https?://\S+", guide)]
        hosts = {urlparse(u).hostname for u in urls}
        for want in ("obsproject.com", "bitfocus.io", "tailscale.com", "discord.com"):
            assert want in hosts, (plat, want, sorted(h for h in hosts if h))


def t_linux_plan_obs_with_ppa():
    steps = m.linux_install_steps(["obs"], which=lambda n: "/usr/bin/" + n)
    assert steps == [
        ("run", ["sudo", "add-apt-repository", "-y", "ppa:obsproject/obs-studio"]),
        ("run", ["sudo", "apt-get", "update"]),
        ("run", ["sudo", "apt-get", "install", "-y", "obs-studio"]),
    ]


def t_linux_plan_obs_without_ppa_tool():
    # Debian has no add-apt-repository, but the index still needs a refresh so a
    # stale apt cache can locate obs-studio. (#408)
    no_ppa = lambda n: None if n == "add-apt-repository" else "/usr/bin/" + n
    assert m.linux_install_steps(["obs"], which=no_ppa) == [
        ("run", ["sudo", "apt-get", "update"]),
        ("run", ["sudo", "apt-get", "install", "-y", "obs-studio"]),
    ]


def t_linux_plan_scripts():
    steps = m.linux_install_steps(["tailscale", "companion"], which=lambda n: "/usr/bin/" + n)
    # companion-pi's bundled node needs libatomic.so.1, absent on a fresh minimal
    # Ubuntu 24.04. Install it after the index refresh and BEFORE the vendor
    # installer, so node can start and the service comes up. (#413)
    assert steps == [
        ("script", "https://tailscale.com/install.sh", ["sh"]),
        ("run", ["sudo", "apt-get", "update"]),
        ("run", ["sudo", "apt-get", "install", "-y", "libatomic1"]),
        ("script",
         "https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh",
         ["sudo", "bash"]),
    ]


def t_linux_plan_companion_libatomic1_precedes_installer():
    # The libatomic1 install must come strictly before the companion-pi script.
    steps = m.linux_install_steps(["companion"], which=lambda n: "/usr/bin/" + n)
    lib = steps.index(("run", ["sudo", "apt-get", "install", "-y", "libatomic1"]))
    script = next(i for i, s in enumerate(steps) if s[0] == "script")
    assert lib < script
    # And it is preceded by an index refresh, for a fresh image. (#408, #413)
    assert ("run", ["sudo", "apt-get", "update"]) in steps[:lib]


def t_linux_plan_obs_and_companion_share_one_update():
    # obs already refreshes the index; the companion libatomic1 step must reuse it,
    # not queue a second `apt-get update`.
    steps = m.linux_install_steps(["obs", "companion"], which=lambda n: "/usr/bin/" + n)
    updates = [s for s in steps if s == ("run", ["sudo", "apt-get", "update"])]
    assert len(updates) == 1
    assert ("run", ["sudo", "apt-get", "install", "-y", "libatomic1"]) in steps


def t_linux_plan_no_libatomic1_without_companion():
    steps = m.linux_install_steps(["obs", "tailscale", "discord"],
                                  which=lambda n: "/usr/bin/" + n, machine="x86_64")
    assert all("libatomic1" not in (s[1] if s[0] == "run" else []) for s in steps)


def t_linux_plan_discord_deb_on_amd64():
    steps = m.linux_install_steps(["discord"], which=lambda n: "/usr/bin/" + n,
                                  machine="x86_64")
    assert steps == [("deb", m.DISCORD_DEB)]
    assert m.DISCORD_DEB.startswith("https://discord.com/")


def t_linux_plan_discord_note_on_arm64():
    # Discord has no native ARM64 Linux .deb and the amd64 one is unsatisfiable, so
    # emit a note instead of a deb step that would error.
    for arch in ("aarch64", "arm64", "armv7l"):
        steps = m.linux_install_steps(["discord"], which=lambda n: "/usr/bin/" + n,
                                      machine=arch)
        assert steps == [("note", m.DISCORD_NO_ARM64_NOTE)], arch
        assert all(s[0] != "deb" for s in steps)


def t_confirmation_parsing():
    assert m.confirmed("y") and m.confirmed("Y") and m.confirmed("yes")
    assert not m.confirmed("") and not m.confirmed("n") and not m.confirmed("nein")


def t_darwin_app_version_reads_short_version():
    # The .app's Info.plist carries the human version in CFBundleShortVersionString.
    plists = {"/Applications/OBS.app/Contents/Info.plist":
              {"CFBundleShortVersionString": "31.0.2", "CFBundleVersion": "31"}}
    v = m.darwin_app_version("obs", exists=lambda p: p in plists,
                             read_plist=lambda p: plists[p])
    assert v == "31.0.2"


def t_darwin_app_version_falls_back_to_bundle_version():
    plists = {"/Applications/Discord.app/Contents/Info.plist":
              {"CFBundleVersion": "0.0.350"}}   # no short string
    assert m.darwin_app_version("discord", exists=lambda p: p in plists,
                                read_plist=lambda p: plists[p]) == "0.0.350"


def t_darwin_app_version_absent_bundle_is_none():
    assert m.darwin_app_version("obs", exists=lambda p: False,
                                read_plist=lambda p: {}) is None


def t_darwin_app_version_unreadable_plist_is_none():
    def boom(_p):
        raise OSError("cannot read")
    assert m.darwin_app_version("obs", exists=lambda p: True, read_plist=boom) is None


def t_app_version_dispatch_darwin():
    plists = {"/Applications/Companion.app/Contents/Info.plist":
              {"CFBundleShortVersionString": "3.99.0"}}
    assert m.app_version("companion", "darwin", exists=lambda p: p in plists,
                         read_plist=lambda p: plists[p]) == "3.99.0"


class _Proc:
    """subprocess.run() result stand-in for CLI version probes."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def t_cli_version_first_nonempty_line():
    # `tailscale version` prints the number on the first line, details below.
    out = _Proc(0, "1.98.5\n  tailscale commit: abc\n  other: def\n")
    assert m.cli_version(["tailscale", "version"], run=lambda *a, **k: out) == "1.98.5"


def t_cli_version_nonzero_or_error_is_none():
    assert m.cli_version(["x", "version"], run=lambda *a, **k: _Proc(1, "")) is None

    def boom(*a, **k):
        raise OSError("not found")
    assert m.cli_version(["x", "version"], run=boom) is None


def t_dpkg_version_reads_stdout():
    out = _Proc(0, "1:30.2.3+dfsg-1\n")
    assert m.dpkg_version("obs-studio", run=lambda *a, **k: out) == "1:30.2.3+dfsg-1"
    assert m.dpkg_version("absent", run=lambda *a, **k: _Proc(1, "")) is None


def t_discord_squirrel_version_picks_highest():
    # Discord's per-user Windows install keeps a version-named app-X.Y.Z folder.
    entries = ["app-0.0.300", "app-0.0.394", "Update.exe", "packages"]
    v = m.discord_squirrel_version(r"C:\Users\x\AppData\Local",
                                   listdir=lambda p: entries)
    assert v == "0.0.394"
    assert m.discord_squirrel_version(r"C:\nope",
                                      listdir=lambda p: (_ for _ in ()).throw(OSError())) is None


def t_build_info_version_reads_json():
    # Discord ships build_info.json (Linux/macOS) with the installed version.
    blob = '{"releaseChannel":"stable","version":"0.0.75"}'
    assert m.build_info_version("/usr/share/discord/resources/build_info.json",
                                read_text=lambda p: blob) == "0.0.75"
    assert m.build_info_version("/x", read_text=lambda p: "not json") is None


def t_app_version_windows_dispatch():
    env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local",
           "ProgramFiles": r"C:\Program Files"}
    # OBS: read the exe's file-version metadata (never launch it).
    obs_exe = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
    v = m.app_version("obs", "win32", env=env, exists=lambda p: p == obs_exe,
                      file_version=lambda p: "32.1.2.0" if p == obs_exe else None)
    assert v == "32.1.2.0"
    # Discord: parse the Squirrel folder.
    assert m.app_version("discord", "win32", env=env,
                         listdir=lambda p: ["app-0.0.394"]) == "0.0.394"
    # Tailscale: a real CLI -> `tailscale version`.
    ts_exe = r"C:\Program Files\Tailscale\tailscale.exe"
    assert m.app_version("tailscale", "win32", env=env, exists=lambda p: p == ts_exe,
                         run=lambda *a, **k: _Proc(0, "1.98.5\n")) == "1.98.5"


def t_companion_http_version_reads_sentry_release():
    # The frontend bundle embeds the release as SENTRY_RELEASE={id:"<ver>+..."}; the
    # shell names the content-hashed bundle, and the modern one wins over -legacy.
    shell = ('<script src="/assets/index-CLsR4s7-.js"></script>'
             '<script src="/assets/index-legacy-B9pHCpUc.js"></script>')
    head = 'var t;e.SENTRY_RELEASE={id:"4.3.4+9244-stable-c14e5e3334"};more'
    calls = []

    def fetch(url, range_bytes):
        calls.append((url, range_bytes))
        return shell if url.endswith("/") else head
    assert m.companion_http_version("http://127.0.0.1:8000", fetch=fetch) == "4.3.4"
    assert calls[1][0].endswith("/assets/index-CLsR4s7-.js") and calls[1][1] == 65536, \
        "the modern bundle was fetched with a bounded Range (not the legacy one, not full)"


def t_companion_http_version_reads_backtick_marker():
    # Companion v5 embeds the release as a template literal rather than a
    # double-quoted string: SENTRY_RELEASE={id:`5.0.1+9649-stable-<sha>`}. The parser
    # must accept both quote styles.
    shell = '<script src="/assets/index-Bp0gTa-1.js"></script>'
    head = 'var t;e.SENTRY_RELEASE={id:`5.0.1+9649-stable-6acd549dd5`};more'
    fetch = lambda u, r: shell if u.endswith("/") else head
    assert m.companion_http_version("http://127.0.0.1:8000", fetch=fetch) == "5.0.1"


def t_companion_http_version_missing_script_or_marker():
    assert m.companion_http_version(
        "http://h", fetch=lambda u, r: "<html>no bundle</html>") is None

    def fetch(u, r):
        return ('<script src="/assets/index-x.js"></script>' if u.endswith("/")
                else "bundle without the marker")
    assert m.companion_http_version("http://h", fetch=fetch) is None


def t_companion_http_version_unreachable_is_none():
    def boom(u, r):
        raise OSError("connection refused")
    assert m.companion_http_version("http://h", fetch=boom) is None


def t_app_version_companion_http_fallback_when_local_missing():
    # Linux has no local Companion version file -> the running server fills it in.
    shell = '<script src="/assets/index-abc.js"></script>'
    head = 'e.SENTRY_RELEASE={id:"4.3.4+1-stable-deadbee"}'
    fetch = lambda u, r: shell if u.endswith("/") else head
    assert m.app_version("companion", "linux", companion_fetch=fetch) == "4.3.4"
    # a present local version is NOT overridden by the HTTP probe (darwin plist wins)
    plists = {"/Applications/Companion.app/Contents/Info.plist":
              {"CFBundleShortVersionString": "4.3.4"}}

    def boom(_u, _r):
        raise AssertionError("HTTP probe must not run when the plist has a version")
    assert m.app_version("companion", "darwin", exists=lambda p: p in plists,
                         read_plist=lambda p: plists[p], companion_fetch=boom) == "4.3.4"


def t_app_version_linux_dispatch():
    # OBS via dpkg, Discord via build_info.json, Tailscale via the CLI.
    assert m.app_version("obs", "linux",
                         run=lambda *a, **k: _Proc(0, "1:30.2.3\n")) == "1:30.2.3"
    bi = "/usr/share/discord/resources/build_info.json"
    assert m.app_version("discord", "linux", exists=lambda p: p == bi,
                         read_text=lambda p: '{"version":"0.0.75"}') == "0.0.75"
    assert m.app_version("tailscale", "linux",
                         run=lambda *a, **k: _Proc(0, "1.98.5\n")) == "1.98.5"


def t_installed_apps_report_aligns_and_marks_unknown():
    lines = m.installed_apps_report(["obs", "discord"],
                                    {"obs": "31.0.2"}.get)
    assert lines[0].startswith("  obs") and "31.0.2" in lines[0]
    # version probe returned None -> a readable placeholder, never an empty column
    assert "(version unavailable)" in lines[1] and lines[1].lstrip().startswith("discord")


def t_app_install_commands_brew_absolute_path():
    assert m.app_install_commands("brew", ["obs"],
                                  brew_path="/opt/homebrew/bin/brew") == \
        [["/opt/homebrew/bin/brew", "install", "--cask", "obs"]]


def t_app_path_candidates():
    env = {"ProgramFiles": r"C:\PF", "ProgramFiles(x86)": r"C:\PF86",
           "LOCALAPPDATA": r"C:\LAD"}
    cands = m.app_path_candidates("obs", "win32", env)
    assert r"C:\PF\obs-studio\bin\64bit\obs64.exe" in cands
    assert r"C:\PF86\obs-studio\bin\64bit\obs64.exe" in cands
    assert m.app_path_candidates("discord", "win32", env) == [r"C:\LAD\Discord\Update.exe"]
    assert m.app_path_candidates("obs", "darwin") == ["/Applications/OBS.app"]
    assert m.app_path_candidates("obs", "linux") == []   # PATH fallback only
    assert m.app_path_candidates("bogus", "darwin") == []


def t_app_update_commands_winget_keeps_companion_interactive():
    cmds = m.app_update_commands("winget", ["obs", "companion"])
    assert len(cmds) == 2
    assert cmds[0][:3] == ["winget", "upgrade", "--id"]
    assert "--interactive" not in cmds[0]
    assert "--interactive" in cmds[1]


def t_app_update_commands_brew_cask_batch():
    assert m.app_update_commands("brew", ["obs", "tailscale"]) == \
        [["brew", "upgrade", "--cask", "obs", "tailscale-app"]]
    assert m.app_update_commands("brew", []) == []


def t_app_update_commands_apt_is_manual_guide():
    assert m.app_update_commands("apt", ["obs"]) == []
    guide = m.apps_update_guide()
    for word in ("obs-studio", "tailscale", "companion-update", "discord.deb"):
        assert word in guide, word


def t_partition_brew_updatable_skips_apps_outside_brew():
    # OBS present on disk but NOT a brew cask goes to 'elsewhere': brew upgrade
    # --cask obs would error 'Cask obs is not installed' and fail the whole batch,
    # so it must be skipped rather than upgraded. (#92)
    managed = {"companion", "tailscale-app", "discord"}   # BREW_CASKS values
    to_up, elsewhere = m.partition_brew_updatable(
        ["obs", "companion", "tailscale", "discord"], managed)
    assert to_up == ["companion", "tailscale", "discord"]
    assert elsewhere == ["obs"]


def t_partition_brew_updatable_probe_failed_keeps_all():
    # managed_casks=None means the `brew list` probe failed, so attempt every
    # present app and skip nothing.
    to_up, elsewhere = m.partition_brew_updatable(["obs", "discord"], None)
    assert to_up == ["obs", "discord"] and elsewhere == []


def t_partition_brew_updatable_none_managed():
    to_up, elsewhere = m.partition_brew_updatable(["obs", "discord"], set())
    assert to_up == [] and elsewhere == ["obs", "discord"]


def t_should_enable_companion_control_only_on_companion_linux():
    # True only when companion is in the just-installed set and no step failed.
    assert m.should_enable_companion_control(["companion"], failed=[]) is True
    assert m.should_enable_companion_control(["obs"], failed=[]) is False
    assert m.should_enable_companion_control(["companion"], failed=["companion ..."]) is False


def t_pacman_plan_installs_the_browser_capable_obs():
    # Plain obs-studio has no CEF, so the relay's HUD/timer Browser Sources stay
    # black. install-apps must not hand an operator that. (#560)
    steps = m.pacman_install_steps(["obs"])
    assert steps == [("run", ["sudo", "pacman", "-S", "--needed", "--noconfirm",
                              "obs-studio-browser"])]
    assert m.PACMAN_APP_PACKAGES["obs"] == "obs-studio-browser"


def t_pacman_plan_tailscale_also_enables_the_service():
    # tailscale's install.sh enables the daemon on the apt path; the Arch package
    # does not, so without this `tailscale up` fails after a reboot.
    steps = m.pacman_install_steps(["tailscale"])
    assert steps[0] == ("run", ["sudo", "pacman", "-S", "--needed", "--noconfirm",
                                "tailscale"])
    assert ("run", ["sudo", "systemctl", "enable", "--now", "tailscaled"]) in steps


def t_pacman_plan_batches_repo_packages_into_one_call():
    steps = m.pacman_install_steps(["obs", "tailscale", "discord"])
    installs = [s for s in steps if s[0] == "run" and s[1][:2] == ["sudo", "pacman"]]
    assert len(installs) == 1
    assert installs[0][1][-3:] == ["obs-studio-browser", "tailscale", "discord"]


def t_pacman_plan_companion_is_a_note_not_a_step():
    # Companion is in no Arch repository and not in the AUR under a usable name,
    # so the plan says so instead of pretending to install it.
    steps = m.pacman_install_steps(["companion"])
    assert [s[0] for s in steps] == ["note"]
    assert "companion" not in m.PACMAN_APP_PACKAGES
    assert "Companion" in steps[0][1]


def t_pacman_plan_never_refreshes_without_upgrading():
    # `-Sy` without `-u` is Arch's partial-upgrade trap.
    for step in m.pacman_install_steps(list(m.APPS)):
        if step[0] == "run":
            assert not any(a.startswith("-Sy") for a in step[1])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
