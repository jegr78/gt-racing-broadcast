#!/usr/bin/env python3
"""Simulated OBS WebSocket server: a stand-in OBS for reproducible screenshots.

Serves a FIXED program image with fixed lenient answers (any scene item exists and
is visible, no inputs, every other request succeeds without effect), so wiki and e2e
captures show a program without the producer's real OBS. It is the lenient mode of
the stateful simulated OBS in tools/event-sim/fakes/obs_sim.py.

    python3 tools/obs-sim.py --image runtime/demo/program.jpg --port 4466 &
    RACECAST_OBS_WS_HOST=127.0.0.1 RACECAST_OBS_WS_PORT=4466 \\
        python3 src/racecast.py relay start

Maintainer tool, not shipped.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "event-sim", "fakes"))
import obs_sim  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Simulated OBS WebSocket server (screenshots).")
    ap.add_argument("--image", required=True, help="program image to serve (jpg or png)")
    ap.add_argument("--port", type=int, default=4466, help="listen port (default 4466)")
    ap.add_argument("--scene", default="Race", help="reported program scene name")
    args = ap.parse_args(argv)
    raw = obs_sim.read_image(args.image)
    sim = obs_sim.ObsSim(None, scene=args.scene, strict=False, image=raw, port=args.port)
    obs_sim.run(sim, banner=f"serving {args.image} ({len(raw)} B) as program scene {args.scene!r}")


if __name__ == "__main__":
    main()
