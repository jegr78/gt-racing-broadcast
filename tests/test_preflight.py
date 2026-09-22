#!/usr/bin/env python3
"""Stdlib unit checks for preflight.py. Run: python3 tests/test_preflight.py"""
import importlib.util, os, socket, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# preflight.py imports its sibling `services` (external_tool_env); in production
# scripts/ is always on sys.path for it, so mirror that for the loader.
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util
spec = importlib.util.spec_from_file_location(
    "preflight", os.path.join(ROOT, "src", "scripts", "preflight.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_classify_ram_boundaries():
    # FAIL below 8 GB, WARN below 12 GB, PASS from 12 GB up. A machine reports
    # 0.1-1.5 GB less than its nominal modules because of firmware and iGPU
    # reservations, so the boundaries carry RAM_SLACK_GB.
    assert m.classify_ram(6.4).level == "FAIL"
    assert m.classify_ram(6.5).level == "WARN"
    assert m.classify_ram(8).level == "WARN"       # 8 GB machine
    assert m.classify_ram(10.4).level == "WARN"
    assert m.classify_ram(10.5).level == "PASS"
    assert m.classify_ram(11.9).level == "PASS"    # physical 12 GB machine
    assert m.classify_ram(15.9).level == "PASS"    # physical 16 GB machine -> green
    assert m.classify_ram(31.9).level == "PASS"    # physical 32 GB machine
    assert "12 GB recommended" in m.classify_ram(9).detail


def t_classify_cpu_boundaries():
    # No GPU (software x264 encode is the core hog): FAIL <4, WARN <6, PASS >=6.
    assert m.classify_cpu(3).level == "FAIL"
    assert m.classify_cpu(4).level == "WARN"
    assert m.classify_cpu(5).level == "WARN"
    assert m.classify_cpu(6).level == "PASS"
    assert m.classify_cpu(8).level == "PASS"


def t_classify_cpu_with_gpu_relaxes_floor():
    # An NVENC GPU offloads the encode, so the floor drops by 2: FAIL <2, WARN <4,
    # PASS >=4, which makes a 4-core machine with an L4 green.
    assert m.classify_cpu(1, has_gpu=True).level == "FAIL"
    assert m.classify_cpu(2, has_gpu=True).level == "WARN"
    assert m.classify_cpu(3, has_gpu=True).level == "WARN"
    assert m.classify_cpu(4, has_gpu=True).level == "PASS"
    assert m.classify_cpu(6, has_gpu=True).level == "PASS"


def t_detect_nvidia_gpu():
    from types import SimpleNamespace
    ok = lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout=b"GPU 0: NVIDIA L4 (UUID: GPU-xxx)", stderr=b"")
    nogpu = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
    which_smi = lambda n: "/usr/bin/nvidia-smi" if n == "nvidia-smi" else None
    which_lspci = lambda n: "/usr/bin/lspci" if n == "lspci" else None
    which_none = lambda n: None
    # nvidia-smi lists a GPU -> True
    assert m.detect_nvidia_gpu(run=ok, which=which_smi) is True
    # nvidia-smi present but no GPU, nothing else -> False
    assert m.detect_nvidia_gpu(run=nogpu, which=which_smi) is False
    # nothing on PATH -> False
    assert m.detect_nvidia_gpu(run=nogpu, which=which_none) is False
    # Linux lspci fallback names an NVIDIA device -> True
    lspci = lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout=b"01:00.0 3D controller: NVIDIA Corporation AD104 [L4]", stderr=b"")
    assert m.detect_nvidia_gpu(run=lspci, which=which_lspci, os_name="posix") is True
    # lspci fallback is Linux-only: same probe under "nt" -> no fallback -> False
    assert m.detect_nvidia_gpu(run=lspci, which=which_lspci, os_name="nt") is False
    # any error is swallowed (best-effort) -> False
    def boom(cmd, **kw):
        raise OSError("boom")
    assert m.detect_nvidia_gpu(run=boom, which=which_smi) is False


def t_classify_disk_boundaries():
    assert m.classify_disk(1).level == "FAIL"
    assert m.classify_disk(4).level == "WARN"
    assert m.classify_disk(5).level == "PASS"


def t_classify_swap_boundaries():
    assert m.classify_swap(0.5).level == "PASS"
    assert m.classify_swap(2).level == "WARN"


def t_readers_return_sane_values():
    assert m.read_ram_bytes() > 0
    assert m.disk_free_bytes(".") > 0
    assert m.read_swap_used_bytes() >= 0


def t_port_free_detects_used_and_free():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.listen(1)
    try:
        assert m.port_free(port) is False
    finally:
        s.close()
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0)); free = s2.getsockname()[1]; s2.close()
    assert m.port_free(free) is True


def t_port_reachable():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.listen(1)
    try:
        assert m.port_reachable("127.0.0.1", port) is True
    finally:
        s.close()
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0)); closed = s2.getsockname()[1]; s2.close()
    assert m.port_reachable("127.0.0.1", closed, timeout=0.3) is False


def t_tool_version_missing():
    assert m.tool_version("definitely-not-a-real-tool-xyz") is None


def t_parse_streamlink_version():
    # `streamlink --version` prints "streamlink X.Y.Z" and a package build may add
    # a suffix, so only the leading X.Y.Z is parsed.
    assert m.parse_streamlink_version("streamlink 8.4.0") == (8, 4, 0)
    assert m.parse_streamlink_version("streamlink 8.2.0") == (8, 2, 0)
    assert m.parse_streamlink_version("streamlink 6.6.2-1") == (6, 6, 2)
    assert m.parse_streamlink_version("streamlink 7.1") == (7, 1, 0)
    assert m.parse_streamlink_version("") is None
    assert m.parse_streamlink_version(None) is None
    assert m.parse_streamlink_version("/usr/bin/streamlink") is None   # path fallback line


def t_classify_streamlink_version_floor():
    # 8.2.0 added --http-cookies-file, which the YouTube serve relies on (#350).
    # An unparseable version yields None, so no extra row is added and the plain
    # PASS from the tool loop stands.
    assert m.classify_streamlink_version("streamlink 6.6.2-1").level == "FAIL"
    assert "8.2.0" in m.classify_streamlink_version("streamlink 6.6.2-1").detail
    assert m.classify_streamlink_version("streamlink 8.1.2").level == "FAIL"
    assert m.classify_streamlink_version("streamlink 8.2.0").level == "PASS"
    assert m.classify_streamlink_version("streamlink 8.4.0").level == "PASS"
    assert m.classify_streamlink_version("streamlink 9.0.0").level == "PASS"
    assert m.classify_streamlink_version("garbage") is None
    assert m.classify_streamlink_version(None) is None


def t_no_window_kwargs_per_os():
    # CREATE_NO_WINDOW only on Windows, empty kwargs elsewhere, so the same call
    # site stays cross-platform.
    assert m.no_window_kwargs("nt") == {"creationflags": 0x08000000}
    assert m.no_window_kwargs("posix") == {}
    assert m.no_window_kwargs("java") == {}


def t_tool_version_hides_console_window():
    # tool_version() runs in-process inside the console-less racecast-ui.exe, so its
    # `<tool> --version` probe MUST carry CREATE_NO_WINDOW or it flashes a terminal
    # per tool on Windows. capture_output keeps the version text. (#23)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"], captured["kw"] = argv, kw
        return _Result("ffmpeg version 6.0\nbuilt with…", "")

    out = m.tool_version("ffmpeg", run=fake_run, which=lambda n: "/x/ffmpeg")
    assert out == "ffmpeg version 6.0"
    assert captured["argv"] == ["ffmpeg", "--version"]
    for k, v in m.no_window_kwargs().items():   # whatever this OS resolves to
        assert captured["kw"].get(k) == v
    # The probe carries a sanitized env so a frozen binary's bundled libs cannot
    # leak into the system-linked tool and crash it on a libcrypto mismatch.
    assert "env" in captured["kw"]
    assert captured["kw"]["env"] == m.external_tool_env()


class _Result:
    def __init__(self, stdout, stderr):
        self.stdout, self.stderr = stdout, stderr


def t_resolve_cookies_overrides():
    assert m.resolve_cookies_path("/x/scripts/preflight.py", None,
                                  "/c/cookies.txt") == "/c/cookies.txt"
    assert m.resolve_cookies_path("/x/scripts/preflight.py", "/run",
                                  None) == os.path.join("/run", "yt-cookies.txt")


def t_resolve_cookies_legacy_fallback():
    """runtime-dir with only cookies.txt (not yet migrated) resolves to it."""
    with tempfile.TemporaryDirectory() as d:
        legacy = os.path.join(d, "cookies.txt")
        open(legacy, "w").close()
        result = m.resolve_cookies_path("/x/scripts/preflight.py", d, None)
        assert result == legacy, f"expected legacy fallback, got {result!r}"


def t_resolve_cookies_prefers_yt_over_legacy():
    """runtime-dir with both yt-cookies.txt and cookies.txt resolves to yt-cookies.txt."""
    with tempfile.TemporaryDirectory() as d:
        yt_ck = os.path.join(d, "yt-cookies.txt")
        legacy = os.path.join(d, "cookies.txt")
        open(yt_ck, "w").close()
        open(legacy, "w").close()
        result = m.resolve_cookies_path("/x/scripts/preflight.py", d, None)
        assert result == yt_ck, f"expected yt-cookies.txt preferred, got {result!r}"


def t_cookies_missing():
    with tempfile.TemporaryDirectory() as d:
        r = m.cookies_status(os.path.join(d, "nope.txt"))
        assert r.level == "WARN"


def t_cookies_old():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cookies.txt")
        with open(p, "w") as fh:
            fh.write("SAPISID\tval")
        m.cookie_jar.record_export(p, now=time.time() - 20 * 3600)
        r = m.cookies_status(p)
        assert r.level == "WARN" and "old" in r.detail.lower()


def t_cookies_fresh_with_marker():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cookies.txt")
        with open(p, "w") as fh:
            fh.write("host\tTRUE\t/\tTRUE\t0\tSAPISID\tval")
        m.cookie_jar.record_export(p)
        r = m.cookies_status(p)
        assert r.level == "PASS"


def t_a_jar_without_an_export_stamp_cannot_be_called_fresh():
    # yt-dlp rewrites the jar on every resolve, so its mtime is not an age. An
    # unstamped jar's age is unknown, and unknown is not a PASS.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cookies.txt")
        with open(p, "w") as fh:
            fh.write("host\tTRUE\t/\tTRUE\t0\tSAPISID\tval")
        r = m.cookies_status(p)
        assert r.level == "WARN" and "cannot be judged" in r.detail, r


def t_cookies_fresh_without_login_warns():
    # An anonymous jar, with no login marker, is a WARN rather than a PASS. (#615)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cookies.txt")
        with open(p, "w") as fh:
            fh.write(".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=40000000\n"
                     ".youtube.com\tTRUE\t/\tTRUE\t0\tSOCS\tCAI\n")
        r = m.cookies_status(p)
        assert r.level == "WARN" and "no logged-in" in r.detail, r


def t_main_returns_int():
    rc = m.main([])
    assert rc in (0, 1)


def t_apps_section_levels():
    rs = m.apps_section(lambda app: True)
    assert [r.level for r in rs] == ["PASS"] * 4
    rs = m.apps_section(lambda app: False)
    by = {r.name: r for r in rs}
    assert by["OBS Studio"].level == "FAIL"          # no broadcast without OBS
    assert by["Companion"].level == "WARN"
    assert by["Tailscale"].level == "WARN"
    assert by["Discord"].level == "WARN"
    assert "racecast install-apps" in by["Discord"].detail


def t_apps_section_web_discord_is_info():
    # Web-variant host: native Discord absent is informational, not a WARN.
    rs = m.apps_section(lambda app: False, web=True)
    disc = [r for r in rs if r.name == "Discord"][0]
    assert disc.level == "INFO" and "Discord-web" in disc.detail
    # The other apps still WARN/FAIL as before when absent.
    obs = [r for r in rs if r.name == "OBS Studio"][0]
    assert obs.level == "FAIL"


def t_install_apps_module_loads():
    ia = m._install_apps_module(os.path.join(ROOT, "src", "scripts"))
    assert callable(ia.app_present)


def t_classify_pipewire_audio_non_linux_skipped():
    # Only Linux uses the PipeWire Application Capture plugin for Discord audio.
    assert m.classify_pipewire_audio("darwin", present=False) is None
    assert m.classify_pipewire_audio("win32", present=True) is None


def t_classify_pipewire_audio_linux_present_passes():
    assert m.classify_pipewire_audio("linux", present=True).level == "PASS"


def t_classify_pipewire_audio_linux_absent_warns():
    r = m.classify_pipewire_audio("linux", present=False)
    assert r.level == "WARN"
    assert "pipewire" in r.detail.lower()        # names the plugin to install


def t_pipewire_audio_candidates_cover_user_and_distro_paths():
    cands = m.pipewire_audio_candidates("/home/op", "aarch64")
    # Fixed Linux paths must stay forward-slash on every OS, including the Windows
    # runner, where os.path.join would inject backslashes. (#97)
    assert all("\\" not in c for c in cands)
    # per-user manual install (dimtpap release tarball layout)
    assert any("/home/op/.config/obs-studio/plugins/linux-pipewire-audio" in c
               for c in cands)
    # distro multiarch path for this arch
    assert any("aarch64-linux-gnu/obs-plugins/linux-pipewire-audio.so" in c
               for c in cands)
    # x86_64 maps to its own multiarch dir
    x = m.pipewire_audio_candidates("/home/op", "x86_64")
    assert any("x86_64-linux-gnu/obs-plugins/linux-pipewire-audio.so" in c for c in x)


def t_pipewire_audio_present_uses_exists_over_candidates():
    cands = ["/a/linux-pipewire-audio.so", "/b/linux-pipewire-audio.so"]
    assert m.pipewire_audio_present(cands, exists=lambda p: p == "/b/linux-pipewire-audio.so")
    assert not m.pipewire_audio_present(cands, exists=lambda p: False)


def t_classify_glibc_levels():
    assert m.classify_glibc((2, 31)).level == "FAIL"   # below deno floor
    assert m.classify_glibc((2, 35)).level == "WARN"   # runs; below binary floor
    assert m.classify_glibc((2, 37)).level == "WARN"
    assert m.classify_glibc((2, 38)).level == "PASS"   # Ubuntu 24.04
    assert m.classify_glibc((2, 39)).level == "PASS"
    # undeterminable glibc (musl/unknown) -> no row
    assert m.classify_glibc(None) is None
    # FAIL/WARN detail names the concrete requirement
    assert "2.35" in m.classify_glibc((2, 31)).detail
    assert "24.04" in m.classify_glibc((2, 35)).detail


def t_gather_linux_includes_pipewire_audio_check():
    # On Linux the Applications section carries the plugin result.
    if not sys.platform.startswith("linux"):
        return
    sections = dict(m.gather(m.__file__, runtime_dir=tempfile.mkdtemp()))
    assert any("PipeWire" in r.name for r in sections["Applications"])


def t_classify_sheet_no_id_warns():
    r = m.classify_sheet(None)
    assert r.level == "WARN"
    assert "RACECAST_SHEET_ID" in r.detail


def t_classify_sheet_generic_error_fails_with_sharing_and_network_hint():
    r = m.classify_sheet("SHEET_ID", "error", "ValueError: boom")
    assert r.level == "FAIL"
    assert "Anyone with the link" in r.detail and "network" in r.detail


def t_classify_sheet_network_warns_without_sharing_blame():
    # A timeout is a NETWORK problem, not a sharing one, so the detail must not tell
    # the operator to fix sharing that was never broken.
    r = m.classify_sheet("SHEET_ID", "network", "the read operation timed out")
    assert r.level == "WARN"
    assert "Anyone with the link" not in r.detail
    assert "network" in r.detail.lower()


def t_classify_sheet_forbidden_fails_with_sharing_hint():
    r = m.classify_sheet("SHEET_ID", "forbidden", "HTTP 403")
    assert r.level == "FAIL"
    assert "Anyone with the link" in r.detail


def t_classify_sheet_not_found_fails_with_wrong_id_hint():
    r = m.classify_sheet("SHEET_ID", "not_found", "HTTP 404")
    assert r.level == "FAIL"
    assert "Sheet ID" in r.detail


def t_fetch_sheet_csv_timeout_maps_to_network():
    # A urlopen TimeoutError must map to a 'network' outcome (WARN, no sharing
    # blame), not the generic sharing FAIL.
    def boom(*a, **k):
        raise TimeoutError("The read operation timed out")
    orig = http_util.urlopen
    http_util.urlopen = boom
    try:
        kind, payload = m.fetch_sheet_csv("SHEET_ID")
    finally:
        http_util.urlopen = orig
    assert kind == "network"
    assert m.classify_sheet("SHEET_ID", kind, payload).level == "WARN"


def t_fetch_sheet_csv_http_403_maps_to_forbidden():
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    orig = http_util.urlopen
    http_util.urlopen = boom
    try:
        kind, _payload = m.fetch_sheet_csv("SHEET_ID")
    finally:
        http_util.urlopen = orig
    assert kind == "forbidden"


def t_classify_sheet_html_body_is_signin_page():
    r = m.classify_sheet("SHEET_ID", "ok", "<HTML><HEAD><TITLE>Sign in</TITLE>")
    assert r.level == "FAIL"
    assert "sign-in" in r.detail


def t_classify_sheet_csv_passes_with_row_count():
    r = m.classify_sheet("SHEET_ID", "ok", '"url","name"\n"https://x","Max"\n')
    assert r.level == "PASS"
    assert "2 row" in r.detail


def t_classify_sheet_empty_csv_fails():
    r = m.classify_sheet("SHEET_ID", "ok", "\n , \n")
    assert r.level == "FAIL"
    assert "empty" in r.detail


def t_classify_sheet_bom_prefixed_html_still_detected():
    r = m.classify_sheet("SHEET_ID", "ok", "﻿<!DOCTYPE html><title>Sign in</title>")
    assert r.level == "FAIL"
    assert "sign-in" in r.detail


def t_speedtest_max_age_env(monkeypatch=None):
    import os
    os.environ.pop("RACECAST_SPEEDTEST_MAX_AGE_DAYS", None)
    assert m._speedtest_max_age() == 7.0                 # default
    os.environ["RACECAST_SPEEDTEST_MAX_AGE_DAYS"] = "3"
    assert m._speedtest_max_age() == 3.0
    os.environ["RACECAST_SPEEDTEST_MAX_AGE_DAYS"] = "junk"
    assert m._speedtest_max_age() == 7.0                 # bad value -> default
    os.environ["RACECAST_SPEEDTEST_MAX_AGE_DAYS"] = "-1"
    assert m._speedtest_max_age() == 7.0                 # non-positive -> default
    os.environ.pop("RACECAST_SPEEDTEST_MAX_AGE_DAYS", None)


def t_obs_benchmark_max_age_env():
    import os
    os.environ.pop("RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS", None)
    assert m._obs_benchmark_max_age() == 30.0            # default
    os.environ["RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS"] = "5"
    assert m._obs_benchmark_max_age() == 5.0
    os.environ["RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS"] = "junk"
    assert m._obs_benchmark_max_age() == 30.0            # bad value -> default
    os.environ["RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS"] = "0"
    assert m._obs_benchmark_max_age() == 30.0            # non-positive -> default
    os.environ.pop("RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS", None)


def t_hardware_section_reports_the_obs_benchmark():
    import tempfile, time
    import obs_benchmark as ob
    d = tempfile.mkdtemp()
    line = [r for r in dict(m.gather(m.__file__, runtime_dir=d))["Hardware"]
            if r.name == "OBS benchmark"]
    assert len(line) == 1 and line[0].level == "INFO"     # nothing measured yet
    full = {"fps_avg": 45.0, "encoder_speed": 1.0, "playback_rate": 1.0, "stall_fraction": 0.0}
    robust = {"fps_avg": 60.0, "encoder_speed": 1.0, "playback_rate": 1.0, "stall_fraction": 0.0}
    ob.append_record({"ts": int(time.time()) - 2 * 86_400, "feed": "A",
                      "full": full, "robust": robust,
                      "verdict": ob.verdict(full, robust, 60.0)}, d)
    line = [r for r in dict(m.gather(m.__file__, runtime_dir=d))["Hardware"]
            if r.name == "OBS benchmark"]
    assert line[0].level == "WARN"
    assert "ROBUST recovers" in line[0].detail and "2 d ago" in line[0].detail


def t_network_section_has_bandwidth_and_advisory():
    import tempfile
    sections = dict(m.gather(m.__file__, runtime_dir=tempfile.mkdtemp()))
    net = sections["Network"]
    # first entry is the measured/INFO bandwidth result, advisory remains last
    assert net[0].name == "bandwidth"
    assert any("wired connection" in r.detail for r in net)


def t_companion_probe_hosts_loopback_default():
    # No bind_ip and no Tailscale IP known -> probe loopback only.
    assert m.companion_probe_hosts(None, None) == ["127.0.0.1"]
    assert m.companion_probe_hosts("", "") == ["127.0.0.1"]


def t_companion_probe_hosts_includes_bind_and_tailscale():
    # racecast binds Companion to the Tailscale IP, not loopback, so a 127.0.0.1-only
    # probe false-negatives and the bind_ip plus Tailscale IP must be probed too.
    # 100.64.0.0/10 are Tailscale CGNAT test constants, never a real address.
    hosts = m.companion_probe_hosts("100.64.0.5", "100.64.0.7")
    assert hosts[0] == "100.64.0.5"
    assert "100.64.0.7" in hosts
    assert "127.0.0.1" in hosts       # always kept as a fallback


def t_companion_probe_hosts_wildcard_and_dedup():
    assert m.companion_probe_hosts("0.0.0.0", None) == ["127.0.0.1"]          # wildcard -> loopback
    assert m.companion_probe_hosts("127.0.0.1", "127.0.0.1") == ["127.0.0.1"]  # de-duplicated
    assert m.companion_probe_hosts("100.64.0.5", "100.64.0.5") == ["100.64.0.5", "127.0.0.1"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
