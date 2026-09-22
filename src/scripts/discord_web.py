#!/usr/bin/env python3
"""Decide when interview audio comes from Discord-web in a browser instead of a
native Discord client, and which browser process the OBS capture targets.

Native Discord is unavailable on some Linux hosts, notably ARM64, where the
official .deb is amd64-only. There the OBS "Discord Audio Capture" source is
retargeted to the browser running Discord-web. The source TYPE is unchanged
(pipewire_audio_application_capture), so only the capture target differs and the
panel and Companion mute and volume bindings keep working.

Pure and stdlib-only. Tests: tests/test_discord_web.py."""
import os
import shutil
import subprocess
import sys

# Native Discord install markers on Linux (mirror install_apps._LINUX_APP_PATHS).
_LINUX_DISCORD_PATHS = ("/usr/share/discord", "/usr/bin/discord")
# Browser process name -> the pipewire_audio_application_capture TargetName to
# emit, tried in order when auto-detecting a running browser.
# `pgrep -x` matches exact names, so "firefox-esr" is not matched here. That is
# fine: resolve_browser() falls back to DEFAULT_BROWSER, which is the correct
# PipeWire TargetName for Firefox-ESR too.
_BROWSER_PROBES = (("firefox", "Firefox"), ("chromium", "Chromium"),
                   ("chrome", "Google Chrome"))
DEFAULT_BROWSER = "Firefox"

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def native_installed(platform=None, which=shutil.which, exists=os.path.exists):
    """True iff a native Discord client is present. The web fallback is Linux-only,
    so a non-Linux platform always returns True. On Linux it looks for a discord
    binary on PATH or a known install path."""
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return True
    if which("discord") or which("Discord"):
        return True
    return any(exists(p) for p in _LINUX_DISCORD_PATHS)


def use_web(platform, env, native_installed_fn=native_installed):
    """Whether to use the Discord-web browser capture variant. Precedence: the
    RACECAST_DISCORD_WEB override (1 -> True, 0 -> False), then auto, which is
    Linux AND no native Discord. Non-Linux is never web under auto."""
    override = (env.get("RACECAST_DISCORD_WEB") or "").strip().lower()
    if override in _TRUE:
        return True
    if override in _FALSE:
        return False
    if not platform.startswith("linux"):
        return False
    return not native_installed_fn(platform)


def detect_running_browser(run=subprocess.run):
    """The TargetName of a running browser (Firefox, Chromium or Chrome), or None.
    Best-effort `pgrep -x`: a probe that errors is skipped, and no match returns
    None."""
    for proc, target in _BROWSER_PROBES:
        try:
            out = run(["pgrep", "-x", proc], capture_output=True, text=True,
                      timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return target
    return None


def resolve_browser(env, running=None):
    """pipewire TargetName for the Discord-web browser, in precedence order: an
    explicit RACECAST_DISCORD_WEB_BROWSER, a detected running browser, then
    DEFAULT_BROWSER."""
    override = (env.get("RACECAST_DISCORD_WEB_BROWSER") or "").strip()
    if override:
        return override
    return running or DEFAULT_BROWSER
