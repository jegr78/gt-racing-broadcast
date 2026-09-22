#!/usr/bin/env python3
"""Stdlib checks for the container install-test command builder. No Docker required.
Run: python3 tests/test_install_container.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "tic", os.path.join(ROOT, "tools", "test-install-container.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_build_command_shape():
    cmd = m.build_container_command("docker", "ubuntu:24.04", "/repo", "/tmp/rt")
    assert cmd[:4] == ["docker", "run", "--rm", "-v"]
    assert "/repo:/repo" in cmd
    assert "ubuntu:24.04" in cmd
    assert cmd[-3] == "bash" and cmd[-2] == "-lc"
    script = cmd[-1]
    # Runs install_tools.py directly rather than through `racecast install-tools`,
    # because racecast forces its own --runtime-dir and would drop the tools in the
    # mounted repo instead of the isolated runtime dir.
    assert "python3 src/scripts/install_tools.py --runtime-dir /tmp/rt" in script
    assert "src/racecast.py" not in script    # not the wrapper: it overrides --runtime-dir
    assert "/tmp/rt/bin" in script            # managed bin on PATH for the asserts
    # asserts every tool is actually runnable
    for probe in ("yt-dlp --version", "deno --version",
                  "streamlink --version", "ffmpeg -version"):
        assert probe in script, probe
    # bootstraps python3 in the base image
    assert "apt-get install -y python3" in script


def t_build_command_honours_podman():
    cmd = m.build_container_command("podman", "ubuntu:24.04", "/x")
    assert cmd[0] == "podman"
    assert "/x:/repo" in cmd
    assert "/tmp/rc-rt" in cmd[-1]            # default runtime dir


def t_build_command_platform_flag():
    # by default no --platform, so the host arch
    default = m.build_container_command("docker", "ubuntu:24.04", "/repo")
    assert not any(a.startswith("--platform") for a in default)
    # --amd64 forces linux/amd64, placed right after `run` and before --rm
    amd64 = m.build_container_command("docker", "ubuntu:24.04", "/repo",
                                      platform="linux/amd64")
    assert amd64[:3] == ["docker", "run", "--platform=linux/amd64"]
    assert "--rm" in amd64 and "/repo:/repo" in amd64
    # the in-container script is unchanged; the emulated image decides the arch
    assert amd64[-3:] == default[-3:]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
