#!/usr/bin/env python3
"""Render the event walkthrough film from a record run (maintainer tool, #1312).

Reads the recording directory that `event_sim.py --record DIR` wrote (timeline.json,
pages/, program/), and the scenario file for the title cards of a recording made before
them, and writes into runtime/event-video/<run>/: event-walkthrough.mp4,
chapters.txt and captions.vtt, plus a work/ dir with the transcoded recordings and the
finished segments, so a killed render resumes and a changed stage renders again without a
new simulation run.

Needs the Playwright Python library with its Chromium, and ffmpeg/ffprobe on PATH (no
drawtext). The music bed is not committed: pass --music; without it the film is silent.

    runtime/pw-venv/bin/python tools/build-event-video.py RECORD_DIR --music runtime/trailer/assets/the-theme.mp3
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "event-video"))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "scripts"))
import film_plan  # noqa: E402
import film_render  # noqa: E402


def film_seconds(text):
    """Seconds of the film from `95.5`, `1:35` or `1:01:35`."""
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a film time: {text!r} (seconds, m:ss or h:mm:ss)") from None
    if total < 0:
        raise argparse.ArgumentTypeError("a film time is not negative")
    return total


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Render the event walkthrough film from a record run's directory.")
    ap.add_argument("dir", help="the record dir (event_sim.py --record DIR) with timeline.json")
    ap.add_argument("--out", help="output dir (default runtime/event-video/<name of DIR>)")
    ap.add_argument("--workers", type=int, default=4, help="parallel capture workers (default 4)")
    ap.add_argument("--from", dest="start", type=film_seconds, help="render from this film time (s, m:ss)")
    ap.add_argument("--to", dest="stop", type=film_seconds, help="render up to this film time (s, m:ss)")
    ap.add_argument("--music", help="music bed, looped quietly under the film; without it the film is silent")
    ap.add_argument("--wait-speed", type=float, default=film_plan.WAIT_SPEED,
                    help=f"speed-up of waiting time (default {film_plan.WAIT_SPEED:g})")
    ap.add_argument("--plan-only", action="store_true", help="write chapters.txt and captions.vtt, render nothing")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.workers < 1:
        ap.error("--workers is at least 1")
    if args.wait_speed < 1:
        ap.error("--wait-speed is at least 1")
    return args


def out_dir(args):
    if args.out:
        return os.path.abspath(args.out)
    name = os.path.basename(os.path.normpath(os.path.abspath(args.dir)))
    return os.path.join(REPO_ROOT, "runtime", "event-video", name)


def _clock(seconds):
    return film_plan.clock(seconds)


def main(argv=None, render=film_render.render):
    import logsetup
    logsetup.harden_stdio()
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["--worker"] and len(argv) == 2:
        return film_render.worker_main(argv[1])
    args = parse_args(argv)
    try:
        timeline = film_plan.load_timeline(args.dir)
        options = {"wait_speed": args.wait_speed}
        plan = film_plan.Plan(timeline, **options)
        frames = plan.frame_range(args.start, args.stop)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot plan the film: {exc}", file=sys.stderr)
        return 2
    if args.music and not os.path.isfile(args.music):
        print(f"no music bed at {args.music}", file=sys.stderr)
        return 2
    out = out_dir(args)
    rec = os.path.realpath(args.dir)
    if os.path.commonpath([rec, os.path.realpath(out)]) == rec:
        print(f"the output dir {out} lies in the recording; pass another --out", file=sys.stderr)
        return 2
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "chapters.txt"), "w", encoding="utf-8") as fh:
        fh.write(film_plan.chapters_txt(plan.chapters()))
    with open(os.path.join(out, "captions.vtt"), "w", encoding="utf-8") as fh:
        fh.write(film_plan.captions_vtt(plan.cues()))
    a, b = frames
    print(f"film {_clock(plan.duration)} ({plan.frames} frames at {plan.fps} fps) from "
          f"{_clock(plan.t1 - plan.t0)} of run; {len(plan.chapters())} chapters, {len(plan.cues())} captions; "
          f"views: {', '.join(plan.views)}")
    print(f"rendering frames {a}-{b} ({_clock((b - a) / plan.fps)}) with {args.workers} workers into {out}"
          + ("" if args.music else "; no --music, the film is silent"))
    if args.plan_only:
        return 0
    return render(plan, timeline, os.path.abspath(args.dir), out, frames, workers=args.workers,
                  music=args.music, options=options, script=os.path.abspath(__file__))


if __name__ == "__main__":
    sys.exit(main())
