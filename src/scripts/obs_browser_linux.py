#!/usr/bin/env python3
"""`racecast obs-browser` — build & install OBS Studio's Browser Source plugin
(obs-browser + its bundled Chromium Embedded Framework) from source on Linux.

Why this exists: the distro `obs-studio` package on Ubuntu is built with the
browser plugin DISABLED (no CEF), and there is no prebuilt OBS-with-browser for
**aarch64** anywhere (the OBS PPA is amd64-only, Flathub has no aarch64 build, no
arm64 snap). Without a Browser Source, the relay-served HUD/timer overlays cannot
be added to OBS. This command builds the plugin against the distro's `libobs-dev`
(ABI-matched) plus OBS's own patched CEF for the platform, and installs it next to
the distro OBS.

The CEF version + per-arch download hash are pinned per OBS major.minor (read from
OBS's `CMakePresets.json` for that release tag); the obs-browser source is pulled
at the submodule commit obs-studio pins for the same release. This module keeps the
pure decision logic (arch/version/asset resolution, detection, the BrowserHWAccel
guidance) separate from the heavy orchestration so the former stays unit-tested.

Tests: tests/test_obs_browser_linux.py
"""
import hashlib, os, shutil, subprocess, sys, tempfile
import http_util

# --- pinned build spec, keyed by OBS "<major>.<minor>" -------------------
# Values taken from obs-studio's CMakePresets.json (configurePresets[dependencies]
# .vendor["obsproject.com/obs-studio"].dependencies.cef) at the matching release
# tag, and the obs-browser submodule commit at that tag. CEF lives on OBS's CDN.
CEF_BASE_URL = "https://cdn-fastly.obsproject.com/downloads"

CEF_SPECS = {
    "32.1": {
        "cef_version": "6533",
        "base_url": CEF_BASE_URL,
        "obs_tag": "32.1.0",
        "obs_browser_commit": "ea04212e4bbadd077f9e6038758c4e4779c24fa3",
        # per normalized arch: the obs CMakePresets target name, revision + sha256
        "targets": {
            "aarch64": {"obs_target": "ubuntu-aarch64", "revision": "6",
                        "sha256": "642514469eaa29a5c887891084d2e73f7dc2d7405f7dfa7726b2dbc24b309999"},
            "x86_64": {"obs_target": "ubuntu-x86_64", "revision": "6",
                       "sha256": "7963335519a19ccdc5233f7334c5ab023026e2f3e9a0cc417007c09d86608146"},
        },
    },
}

# apt build dependencies (the distro ships an ABI-matched libobs-dev + the OBS
# CMake config packages that export OBS::libobs / OBS::obs-frontend-api / …).
BUILD_APT_DEPS = (
    "cmake", "ninja-build", "pkg-config", "build-essential",
    "libobs-dev", "qt6-base-dev", "nlohmann-json3-dev",
    "libx11-dev", "libxcb1-dev", "libxcomposite-dev", "libxdamage-dev",
    "libxfixes-dev", "libgles2-mesa-dev", "libegl1-mesa-dev", "libdrm-dev",
)

# obs-studio finder modules fetched at build time (not vendored: GPL + must match
# the release tag). Placed on the wrapper's CMAKE_MODULE_PATH.
OBS_FINDERS = ("FindCEF.cmake", "FindLibdrm.cmake")

OBS_BROWSER_GIT = "https://github.com/obsproject/obs-browser.git"
OBS_DATA_DIR = "/usr/share/obs/obs-plugins/obs-browser"

_AARCH64 = ("aarch64", "arm64")
_X86_64 = ("x86_64", "amd64")


# --- pure helpers --------------------------------------------------------
def normalize_arch(machine):
    """Map a platform.machine() value to the canonical CEF arch key, or None."""
    m = (machine or "").lower()
    if m in _AARCH64:
        return "aarch64"
    if m in _X86_64:
        return "x86_64"
    return None


def arch_triplet(arch):
    """The Debian multiarch triplet for an OBS plugin dir."""
    return {"aarch64": "aarch64-linux-gnu", "x86_64": "x86_64-linux-gnu"}[arch]


def obs_version_key(version):
    """'32.1.0-0ubuntu3' -> '32.1'; None if it has no major.minor."""
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return f"{parts[0]}.{parts[1]}"


def resolve_spec(obs_version):
    """The pinned CEF/obs-browser spec for an OBS version, or None if unsupported."""
    key = obs_version_key(obs_version)
    return CEF_SPECS.get(key) if key else None


def _target(spec, arch):
    return spec["targets"][arch]


def cef_filename(spec, arch):
    """The CEF archive name, e.g. cef_binary_6533_linux_aarch64_v6.tar.xz.
    Mirrors obs-studio's setup_ubuntu naming (ubuntu-<arch> -> linux_<arch>)."""
    t = _target(spec, arch)
    linux_target = t["obs_target"].replace("ubuntu-", "linux_")
    rev = f"_v{t['revision']}" if t.get("revision") else ""
    return f"cef_binary_{spec['cef_version']}_{linux_target}{rev}.tar.xz"


def cef_url(spec, arch):
    return f"{spec['base_url']}/{cef_filename(spec, arch)}"


def cef_sha256(spec, arch):
    return _target(spec, arch)["sha256"]


def obs_plugins_dir(arch):
    return f"/usr/lib/{arch_triplet(arch)}/obs-plugins"


# Distros disagree on where OBS plugins live: Debian/Ubuntu use a multiarch
# triplet, Arch (and other non-multiarch distros) a plain /usr/lib/obs-plugins.
OBS_PLUGINS_DIR_PLAIN = "/usr/lib/obs-plugins"


def obs_plugins_dirs(arch):
    """Every directory a distro may place OBS plugins in, for presence checks.
    Fixed-OS Linux paths — built with explicit '/' (CLAUDE.md cross-platform rule)."""
    return [obs_plugins_dir(arch), OBS_PLUGINS_DIR_PLAIN]


def browser_plugin_present(plugins_dirs, exists=os.path.exists):
    """True iff obs-browser.so is in ANY of `plugins_dirs`. Checking only the
    Debian path reported a missing Browser Source on distros that had one."""
    return any(browser_plugin_installed(d, exists) for d in plugins_dirs)


def browser_plugin_installed(plugins_dir, exists=os.path.exists):
    """True iff obs-browser.so is present in the OBS plugins dir. plugins_dir is a
    fixed-OS Linux path, so join with an explicit '/': os.path.join would inject a
    backslash on the Windows test runner (CLAUDE.md cross-platform rule)."""
    return exists(plugins_dir.rstrip("/") + "/obs-browser.so")


def install_hint(machine, obs_present, browser_present):
    """The one-line pointer install-apps prints when OBS is installed on a
    supported Linux arch but its Browser Source plugin is missing — else None."""
    arch = normalize_arch(machine)
    if arch not in CEF_SPECS["32.1"]["targets"]:
        return None
    if not obs_present or browser_present:
        return None
    return ("OBS has no Browser Source plugin (needed for the relay HUD/timer "
            "overlays) — run `racecast obs-browser` to build & install it.")


PACMAN_BROWSER_PACKAGE = "obs-studio-browser"


def pacman_redirect_note(has_pacman, has_apt):
    """Operator note for Arch-based hosts, or None when this isn't one.

    Everything below (BUILD_APT_DEPS, _detect_obs_version's dpkg-query, the
    Debian multiarch plugin path) is apt-specific: this command exists because
    Debian/Ubuntu ship no browser plugin for aarch64 and there is nothing to
    install. Arch is the opposite — a prebuilt CEF-enabled OBS is one package
    away — so a source build here would be slow, unnecessary, and would fail
    anyway. `has_apt` wins when both are present, mirroring
    install_tools.pick_manager so one host never gets two answers."""
    if not has_pacman or has_apt:
        return None
    return (
        "On Arch the Browser Source comes as a package, not a source build.\n"
        f"  sudo pacman -S {PACMAN_BROWSER_PACKAGE}\n"
        "It replaces the plain obs-studio package (which is built without CEF, so "
        "every Browser Source is missing and the relay's HUD/timer overlays stay "
        "black). Verify afterwards with:\n"
        f"  pacman -Ql {PACMAN_BROWSER_PACKAGE} | grep obs-browser.so\n"
        "This command is for Debian/Ubuntu, where no prebuilt plugin exists.")


def cef_configure_argv(cef_src, build_dir, arch):
    """cmake configure argv for the CEF dll-wrapper build. On aarch64 it must
    force -DPROJECT_ARCH=arm64: CEF's CMake checks for 'arm64' but Linux reports
    'aarch64', so it otherwise mis-detects x86_64 and emits -m64 -march=x86-64."""
    argv = ["cmake", "-S", cef_src, "-B", build_dir, "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release"]
    if arch == "aarch64":
        argv.append("-DPROJECT_ARCH=arm64")
    return argv


def plugin_configure_argv(standalone_src, build_dir, obs_browser_src, cef_root):
    """cmake configure argv for the standalone obs-browser plugin build."""
    return ["cmake", "-S", standalone_src, "-B", build_dir, "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_BROWSER=ON",
            f"-DOBS_BROWSER_SRC={obs_browser_src}",
            f"-DCEF_ROOT_DIR={cef_root}"]


def browser_hwaccel_note():
    """Operator guidance for no-GPU / VM hosts where CEF's GPU subprocess crashes
    ('Unable to open DRM render node') unless hardware acceleration is disabled."""
    return ("On a host without a GPU (a VM, headless/no DRM render node), CEF's GPU "
            "subprocess crashes. If the Browser Source stays black or OBS is "
            "unstable, disable browser hardware acceleration: OBS → Settings → "
            "Advanced → uncheck 'Browser Source Hardware Acceleration' "
            "(or set BrowserHWAccel=false in ~/.config/obs-studio/global.ini).")


def set_browser_hwaccel(global_ini_text, enabled):
    """Return global.ini text with [General] BrowserHWAccel set to enabled.
    Idempotent; adds the key (and a [General] section) if absent."""
    value = "true" if enabled else "false"
    lines = global_ini_text.splitlines()
    out, seen, in_general, general_at = [], False, False, None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_general = (stripped == "[General]")
            if in_general:
                general_at = len(out)
        if in_general and stripped.split("=", 1)[0].strip() == "BrowserHWAccel":
            out.append(f"BrowserHWAccel={value}")
            seen = True
            continue
        out.append(line)
    if not seen:
        if general_at is None:
            out = ["[General]", f"BrowserHWAccel={value}"] + out
        else:
            out.insert(general_at + 1, f"BrowserHWAccel={value}")
    return "\n".join(out) + ("\n" if global_ini_text.endswith("\n") or not global_ini_text else "")


# --- orchestration (not run in CI) ---------------------------------------
def _wrapper_dir():
    """The shipped wrapper CMakeLists dir (repo src/ or frozen bundle)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "obs", "obs-browser-build"))


def _run(argv, **kw):
    print("  $", " ".join(argv))
    return subprocess.run(argv, check=True, **kw)


def _download(url, dest):
    print(f"  downloading {url}")
    with http_util.open_url(url, timeout=None) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    """CLI entry: build & install obs-browser for the current Linux host."""
    import argparse, platform
    p = argparse.ArgumentParser(prog="racecast obs-browser",
                                description="Build & install the OBS Browser Source plugin from source (Linux).")
    p.add_argument("--yes", "-y", action="store_true", help="don't prompt before the build")
    p.add_argument("--build-dir", help="where to download/build (default: a temp dir)")
    p.add_argument("--keep", action="store_true", help="keep the build dir afterwards")
    args = p.parse_args(argv)

    if sys.platform != "linux":
        print("obs-browser source build is Linux-only "
              "(Windows/macOS OBS ship the Browser Source already).")
        return 1
    # Before any dpkg/apt work: on Arch this whole path is the wrong tool.
    note = pacman_redirect_note(bool(shutil.which("pacman")),
                                bool(shutil.which("apt-get")))
    if note:
        print(note)
        return 1
    arch = normalize_arch(platform.machine())
    spec = None
    obs_version = _detect_obs_version()
    if arch:
        spec = resolve_spec(obs_version) if obs_version else None
    if not arch or arch not in CEF_SPECS["32.1"]["targets"]:
        print(f"Unsupported architecture: {platform.machine()!r}.")
        return 1
    if obs_version is None:
        print("OBS Studio not detected (install it first: `racecast install-apps`).")
        return 1
    if spec is None:
        print(f"No pinned CEF spec for OBS {obs_version}. Supported: "
              + ", ".join(sorted(CEF_SPECS)) + ". See the wiki (ARM64 OBS Browser Source).")
        return 1

    plugins = obs_plugins_dir(arch)
    if browser_plugin_installed(plugins):
        print(f"obs-browser.so already present in {plugins} — nothing to do.")
        print(browser_hwaccel_note())
        return 0

    print(f"Will build obs-browser {spec['obs_browser_commit'][:10]} + CEF "
          f"{spec['cef_version']} ({arch}) against the distro libobs, and install into\n"
          f"  {plugins}\nThis downloads ~340 MB of CEF and compiles for several minutes.")
    if not args.yes:
        try:
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("aborted."); return 1
        except EOFError:
            print("non-interactive: re-run with --yes to proceed."); return 1

    workdir = args.build_dir or tempfile.mkdtemp(prefix="racecast-obs-browser-")
    try:
        return _build_and_install(arch, spec, plugins, workdir)
    finally:
        if not args.keep and not args.build_dir:
            shutil.rmtree(workdir, ignore_errors=True)


def _detect_obs_version():
    """The installed OBS version string via dpkg, or None."""
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "obs-studio"],
                             capture_output=True, text=True, errors="replace")
        v = (out.stdout or "").strip()
        return v or None
    except Exception:
        return None


def _build_and_install(arch, spec, plugins, workdir):
    os.makedirs(workdir, exist_ok=True)
    print(f"== build dir: {workdir}")

    # 1) build deps
    print("== installing build dependencies (sudo apt)")
    _run(["sudo", "apt-get", "install", "-y", "--no-install-recommends", *BUILD_APT_DEPS])

    # 2) CEF: download + verify + extract
    cef_tar = os.path.join(workdir, cef_filename(spec, arch))
    _download(cef_url(spec, arch), cef_tar)
    got = _sha256(cef_tar)
    if got != cef_sha256(spec, arch):
        print(f"CEF checksum mismatch!\n expected {cef_sha256(spec, arch)}\n got      {got}")
        return 2
    cef_dir = os.path.join(workdir, "cef")
    shutil.rmtree(cef_dir, ignore_errors=True); os.makedirs(cef_dir)
    _run(["tar", "--strip-components", "1", "-xJf", cef_tar, "-C", cef_dir])

    # 3) CEF dll wrapper (PROJECT_ARCH fix for aarch64)
    cef_build = os.path.join(cef_dir, "build")
    shutil.rmtree(cef_build, ignore_errors=True)
    _run(cef_configure_argv(cef_dir, cef_build, arch))
    _run(["cmake", "--build", cef_build, "--target", "libcef_dll_wrapper",
          "-j", str(os.cpu_count() or 2)])

    # 4) obs-browser source at the pinned commit
    obs_browser = os.path.join(workdir, "obs-browser")
    shutil.rmtree(obs_browser, ignore_errors=True)
    _run(["git", "clone", "--quiet", OBS_BROWSER_GIT, obs_browser])
    _run(["git", "-C", obs_browser, "checkout", "--quiet", spec["obs_browser_commit"]])

    # 5) assemble the standalone build tree: our wrapper CMakeLists + obs finders
    standalone = os.path.join(workdir, "standalone")
    shutil.rmtree(standalone, ignore_errors=True); os.makedirs(os.path.join(standalone, "finders"))
    shutil.copy(os.path.join(_wrapper_dir(), "CMakeLists.txt"),
                os.path.join(standalone, "CMakeLists.txt"))
    for finder in OBS_FINDERS:
        url = (f"https://raw.githubusercontent.com/obsproject/obs-studio/"
               f"{spec['obs_tag']}/cmake/finders/{finder}")
        _download(url, os.path.join(standalone, "finders", finder))

    # 6) configure + build the plugin
    plug_build = os.path.join(standalone, "build")
    _run(plugin_configure_argv(standalone, plug_build, obs_browser, cef_dir))
    _run(["cmake", "--build", plug_build, "-j", str(os.cpu_count() or 2)])

    # 7) install plugin + CEF runtime + data
    _install_artifacts(arch, spec, plugins, cef_dir, plug_build, obs_browser)
    print("\nDone. obs-browser installed. Restart OBS — a 'Browser' source type "
          "is now available.")
    print(browser_hwaccel_note())
    return 0


def _install_artifacts(arch, spec, plugins, cef_dir, plug_build, obs_browser):
    print(f"== installing into {plugins} (sudo)")
    so = _find(plug_build, "obs-browser.so")
    helper = _find(plug_build, "obs-browser-page")
    _run(["sudo", "install", "-Dm755", so, os.path.join(plugins, "obs-browser.so")])
    _run(["sudo", "install", "-Dm755", helper, os.path.join(plugins, "obs-browser-page")])
    rel = os.path.join(cef_dir, "Release")
    res = os.path.join(cef_dir, "Resources")
    for name in ("libcef.so", "libEGL.so", "libGLESv2.so", "libvk_swiftshader.so",
                 "libvulkan.so.1", "snapshot_blob.bin", "v8_context_snapshot.bin",
                 "vk_swiftshader_icd.json"):
        src = os.path.join(rel, name)
        if os.path.exists(src):
            _run(["sudo", "install", "-Dm644", src, os.path.join(plugins, name)])
    for name in ("chrome_100_percent.pak", "chrome_200_percent.pak", "resources.pak",
                 "icudtl.dat"):
        src = os.path.join(res, name)
        if os.path.exists(src):
            _run(["sudo", "install", "-Dm644", src, os.path.join(plugins, name)])
    _run(["sudo", "mkdir", "-p", os.path.join(plugins, "locales")])
    _run(["sudo", "cp", "-a", os.path.join(res, "locales", "."),
          os.path.join(plugins, "locales") + "/"])
    data = os.path.join(obs_browser, "data")
    if os.path.isdir(data):
        _run(["sudo", "mkdir", "-p", OBS_DATA_DIR])
        _run(["sudo", "cp", "-a", data + "/.", OBS_DATA_DIR + "/"])


def _find(root, name):
    for dirpath, _dirs, files in os.walk(root):
        if name in files:
            return os.path.join(dirpath, name)
    raise FileNotFoundError(f"{name} not found under {root}")


if __name__ == "__main__":
    sys.exit(main())
