#!/usr/bin/env python3
"""Maintainer tool: run the REAL `racecast install-tools` path inside a fresh
`ubuntu:24.04` container and assert every feed tool ends up runnable. This is the
check the e2e harness cannot do, because it stubs the tools; here they are really
downloaded, installed and probed with `<tool> --version`.

Network-heavy (real apt and GitHub downloads) and needs Docker or Podman, so it is
opt-in and NOT part of CI. The command builder below is pure and unit-tested in
tests/test_install_container.py; only main() touches Docker."""
import argparse
import os
import shutil
import subprocess
import sys

IMAGE = "ubuntu:24.04"


def build_container_command(engine, image, repo_dir, runtime_dir="/tmp/rc-rt",
                            platform=None):
    """The `<engine> run` argv that installs the toolchain from source inside the
    container and asserts each tool is runnable. No Docker is touched here. Root in
    the container means install-tools' apt steps take no sudo, which is correct
    because the base image has no sudo binary, and yt-dlp/deno land in
    <runtime_dir>/bin.

    `platform` (e.g. "linux/amd64") forces the container arch, the way to exercise
    the amd64 install path on an arm64 host. Inside an amd64 container
    `platform.machine()` reports x86_64, so install-tools pulls the amd64 yt-dlp and
    deno binaries; the in-container script is identical either way.

    This runs `install_tools.py` DIRECTLY, not `racecast install-tools`: the racecast
    wrapper injects its own --runtime-dir (install-tools is a RUNTIME_DIR_ONESHOT),
    which overrides ours and drops the managed binaries in the mounted repo's
    runtime/bin instead of the isolated <runtime_dir>. Calling the module directly
    honours --runtime-dir, so the download lands where PATH points and the host mount
    stays clean."""
    script = (
        "set -euo pipefail; "
        "apt-get update && apt-get install -y python3 curl ca-certificates; "
        f"cd /repo && python3 src/scripts/install_tools.py --runtime-dir {runtime_dir}; "
        f"export PATH={runtime_dir}/bin:$PATH; "
        "yt-dlp --version && deno --version && "
        "streamlink --version && ffmpeg -version"
    )
    plat = [f"--platform={platform}"] if platform else []
    return [engine, "run"] + plat + ["--rm", "-v", f"{repo_dir}:/repo", image,
            "bash", "-lc", script]


def _pick_engine(which=shutil.which):
    for engine in ("docker", "podman"):
        if which(engine):
            return engine
    return None


def main():
    ap = argparse.ArgumentParser(prog="test-install-container", add_help=True)
    ap.add_argument("--amd64", action="store_true",
                    help="force linux/amd64 (test the amd64 install path on an "
                         "arm64 host, e.g. Apple Silicon + Docker/Rosetta)")
    ap.add_argument("--image", default=IMAGE,
                    help=f"base image (default {IMAGE})")
    a = ap.parse_args()

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    engine = _pick_engine()
    if engine is None:
        sys.exit("Needs Docker or Podman on PATH, neither found.")
    platform = "linux/amd64" if a.amd64 else None
    cmd = build_container_command(engine, a.image, repo_dir, platform=platform)
    print("Running:", " ".join(cmd[:7]), "...")
    rc = subprocess.call(cmd)
    if rc == 0:
        print("PASS: all tools installed and runnable in", a.image,
              "(linux/amd64)" if platform else "")
    else:
        print("FAIL: container install test exited", rc)
    sys.exit(rc)


if __name__ == "__main__":
    main()
