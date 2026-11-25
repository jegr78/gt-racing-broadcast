#!/usr/bin/env python3
"""A loopback RTMP sink for a real OBS in a record run (#1310): OBS streams a Part here
instead of to a platform, and the stream is read and dropped.

    python3 tools/event-sim/fakes/rtmp_sink.py --port PORT [--ffmpeg ffmpeg]

ffmpeg listens on 127.0.0.1:PORT, takes one publisher and ends with it; the sink starts
the next listener for the next Part until it is stopped. Nothing leaves loopback."""
import argparse
import subprocess
import sys
import time

HOST = "127.0.0.1"
APP = "live"
RESTART_S = 0.5


def url(port):
    """The stream target OBS gets (RACECAST_SIM_RTMP); OBS appends its key."""
    return f"rtmp://{HOST}:{port}/{APP}"


def sink_argv(ffmpeg, port):
    return [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-threads", "1",
            "-listen", "1", "-i", url(port) + "/sim", "-c", "copy", "-f", "null", "-"]


def serve(port, ffmpeg="ffmpeg", popen=subprocess.Popen, running=lambda: True, sleep=time.sleep):
    """Listen again after every publisher, while `running()`. Returns the listeners started."""
    started = 0
    while running():
        proc = popen(sink_argv(ffmpeg, port), stdin=subprocess.DEVNULL)
        started += 1
        try:
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        sleep(RESTART_S)
    return started


def main(argv=None):
    ap = argparse.ArgumentParser(description="Loopback RTMP sink for a real OBS in an event-sim record run.")
    ap.add_argument("--port", type=int, required=True, help="the loopback port to listen on")
    ap.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary (default: on PATH)")
    a = ap.parse_args(argv)
    serve(a.port, a.ffmpeg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
