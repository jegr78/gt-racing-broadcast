#!/usr/bin/env python3
"""Pure, import-testable core of the broadcast-asset renderer
(tools/render-assets.py).

An "asset kit" is a per-league folder, normally profiles/<name>/assets-src/,
holding the design and the round's texts:

    kit.json      what to render: screen id -> filename, scene -> duration/audio
    stage.html    one element per still, each with id=<screen id>
    motion.html   optional; window.SCENES + window.renderAt(scene, t)
    event.json    the texts of the current round

Both pages expose window.applyText(cfg) so a round is a text edit, not a
design edit. Everything in this module is stdlib-only and free of I/O side
effects beyond reading the manifest, so tests/test_assets_kit.py can exercise
it without a browser or ffmpeg.
"""
import json
import os

KIT_MANIFEST = "kit.json"
STAGE_PAGE = "stage.html"
MOTION_PAGE = "motion.html"
EVENT_TEXTS = "event.json"

DEFAULT_FPS = 30
FRAME_PATTERN = "f%05d.jpg"
# Rendered frames are an intermediate; JPEG keeps a 40 s scene at a few hundred
# MB instead of several GB, and the H.264 pass below is the quality bottleneck.
FRAME_QUALITY = 95

VIDEO_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-pix_fmt", "yuv420p"]
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k"]


class KitError(Exception):
    """A kit is missing, malformed, or asks for something that isn't there."""


def kit_dir(root, profile):
    """Where a profile's asset kit lives."""
    return os.path.join(root, "profiles", profile, "assets-src")


def load_kit(path):
    """Read and validate <path>/kit.json."""
    manifest = os.path.join(path, KIT_MANIFEST)
    if not os.path.isfile(manifest):
        raise KitError(f"no {KIT_MANIFEST} in {path}")
    try:
        with open(manifest, encoding="utf-8") as fh:
            kit = json.load(fh)
    except (OSError, ValueError) as exc:
        raise KitError(f"cannot read {manifest}: {exc}") from exc
    if not isinstance(kit, dict):
        raise KitError(f"{manifest} must contain a JSON object")
    if not kit.get("stills") and not kit.get("scenes"):
        raise KitError(f"{manifest} declares neither stills nor scenes")
    return kit


def load_event_texts(path):
    """Read <path>/event.json; a missing file simply means 'no overrides'."""
    target = os.path.join(path, EVENT_TEXTS)
    if not os.path.isfile(target):
        return {}
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise KitError(f"cannot read {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise KitError(f"{target} must contain a JSON object")
    return data


def safe_output_name(name):
    """Guard a manifest-supplied filename.

    Filenames come from kit.json, which is repo/profile data rather than
    something the renderer controls, so a value must never walk out of the
    output directory. Spaces are allowed on purpose: the Sheet's Assets tab
    uses labels like "Standby Cover.png" verbatim as the filename.
    """
    if not isinstance(name, str) or not name.strip():
        raise KitError("empty output filename")
    if name != os.path.basename(name) or name in (".", ".."):
        raise KitError(f"output filename must not contain a path: {name!r}")
    # basename() leaves Windows separators alone on POSIX, so reject explicitly.
    if "\\" in name or "/" in name:
        raise KitError(f"output filename must not contain a path: {name!r}")
    return name


def still_targets(kit, only=None):
    """[(screen id, filename)] for the stills to render, sorted by screen id."""
    stills = kit.get("stills") or {}
    if only:
        unknown = [s for s in only if s not in stills]
        if unknown:
            raise KitError("unknown screen id(s): " + ", ".join(sorted(unknown)))
        wanted = {s: stills[s] for s in only}
    else:
        wanted = stills
    return [(sid, safe_output_name(wanted[sid])) for sid in sorted(wanted)]


def scene_names(kit, only=None):
    scenes = kit.get("scenes") or {}
    if only:
        unknown = [s for s in only if s not in scenes]
        if unknown:
            raise KitError("unknown scene(s): " + ", ".join(sorted(unknown)))
        return list(only)
    return sorted(scenes)


def scene_spec(kit, name):
    """Normalized scene spec: duration, fps, output, optional audio."""
    scenes = kit.get("scenes") or {}
    if name not in scenes:
        raise KitError(f"unknown scene: {name}")
    raw = dict(scenes[name])
    if not raw.get("duration"):
        raise KitError(f"scene {name} has no duration")
    raw["fps"] = int(raw.get("fps") or DEFAULT_FPS)
    raw["duration"] = float(raw["duration"])
    raw["output"] = safe_output_name(raw.get("output") or f"{name}.mp4")
    return raw


def frame_count(duration, fps):
    return int(round(float(duration) * int(fps)))


def frame_name(index):
    return FRAME_PATTERN % index


def _num(value):
    """Render a number without a trailing .0, so filters read like the docs."""
    f = float(value)
    return str(int(f)) if f == int(f) else str(f)


def audio_filter(audio):
    """ffmpeg -af chain for the fades declared on a scene's audio spec."""
    if not audio:
        return None
    parts = []
    if audio.get("fadeIn"):
        parts.append(f"afade=t=in:st=0:d={_num(audio['fadeIn'])}")
    out = audio.get("fadeOut")
    if out:
        parts.append(
            f"afade=t=out:st={_num(out['start'])}:d={_num(out['duration'])}")
    return ",".join(parts) or None


def mux_args(frames_dir, fps, out_path, audio_path=None, audio=None):
    """Full ffmpeg argv (without the leading 'ffmpeg') for one scene.

    Returned as a list so every path stays one argv entry: a space or a quote
    in a filename can never split into extra arguments.
    """
    args = ["-hide_banner", "-v", "warning", "-y",
            "-framerate", str(fps),
            "-i", os.path.join(frames_dir, FRAME_PATTERN)]
    filt = audio_filter(audio) if audio else None
    if audio_path:
        spec = audio or {}
        # -ss/-t belong BEFORE -i: as input options they seek instead of
        # decoding the whole track and throwing most of it away.
        if spec.get("start") is not None:
            args += ["-ss", _num(spec["start"])]
        if spec.get("duration") is not None:
            args += ["-t", _num(spec["duration"])]
        args += ["-i", audio_path]
        if filt:
            args += ["-filter_complex", f"[1:a]{filt}[a]", "-map", "0:v",
                     "-map", "[a]"]
        else:
            args += ["-map", "0:v", "-map", "1:a"]
    args += VIDEO_ARGS + ["-r", str(fps)]
    if audio_path:
        args += AUDIO_ARGS + ["-shortest"]
    args += ["-movflags", "+faststart", out_path]
    return args


def text_config(kit, event, overrides):
    """The text config handed to window.applyText().

    Precedence: kit defaults < event.json < command line. Keys starting with
    "_" are documentation inside the JSON files and never reach the page.
    """
    merged = {}
    for source in (kit.get("text") or {}, event or {}):
        for key, value in source.items():
            if not key.startswith("_"):
                merged[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            merged[key] = value
    return merged
