#!/usr/bin/env python3
"""Neutral placeholders for missing broadcast assets.

When the OBS scene collection references a graphic or Intro/Outro clip a league
never provided, such as weather overlays it does not use, drop a byte-identical
copy of a bundled neutral placeholder under the expected filename so OBS shows a
neutral source instead of a black one.

Pure stdlib. It never imports config.py, the same dependency-light contract as the
relay scripts that call it. The bundled assets live at src/assets/placeholders/ and
ship inside the binary because src/assets is in build-binary.py's DATA list."""
import os, re, shutil

GRAPHIC_PLACEHOLDER = "transparent-1080p.png"
MEDIA_PLACEHOLDER = "neutral-5s-1080p.mp4"
MUSIC_PLACEHOLDER = "neutral-ambient-loop.mp3"

# The captured name must be a bare basename, so forbid '/', '\\' and '"': a
# crafted template ref like __RACECAST_GRAPHICS__/../../x.png must not yield a
# name that escapes the graphics dir when joined for a read or write.
_GRAPHICS_REF_RE = re.compile(r"__RACECAST_GRAPHICS__/([^\"\\/]+\.png)")
_MEDIA_REF_RE = re.compile(r"__RACECAST_MEDIA__/([^\"\\/]+\.(?:mp4|mp3|m4a|wav|ogg))")
_OBS_TEMPLATE_NAMES = ("GT_Racing_Endurance.template.json", "GT_Racing_Endurance.json")


def _placeholders_dir():
    """src/assets/placeholders resolved relative to THIS module, so it works in the
    repo and under _MEIPASS, where src/scripts and src/assets ship side by side."""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "assets", "placeholders"))


def graphic_placeholder_path():
    """Absolute path of the bundled transparent PNG, or None when absent."""
    p = os.path.join(_placeholders_dir(), GRAPHIC_PLACEHOLDER)
    return p if os.path.isfile(p) else None


def media_placeholder_path():
    """Absolute path of the bundled neutral clip, or None when absent."""
    p = os.path.join(_placeholders_dir(), MEDIA_PLACEHOLDER)
    return p if os.path.isfile(p) else None


def music_placeholder_path():
    """Absolute path of the bundled synthetic ambient loop, or None when absent."""
    p = os.path.join(_placeholders_dir(), MUSIC_PLACEHOLDER)
    return p if os.path.isfile(p) else None


def media_placeholder_for(name):
    """Pick the right bundled placeholder for a media filename: the ambient loop
    for an audio (.mp3) file, the neutral clip for everything else."""
    return music_placeholder_path() if name.lower().endswith(".mp3") else media_placeholder_path()


def expected_graphics_from_template(text):
    """Sorted unique '<name>.png' from every __RACECAST_GRAPHICS__/<name>.png
    reference in the raw JSON collection text."""
    return sorted(set(_GRAPHICS_REF_RE.findall(text)))


def expected_media_from_template(text):
    """Sorted unique '<name>' from every __RACECAST_MEDIA__/<name> reference in the
    raw JSON collection text, such as intro.mp4 or intermission.mp3."""
    return sorted(set(_MEDIA_REF_RE.findall(text)))


def find_obs_template(obs_dir):
    """First existing OBS template in obs_dir, preferring the package
    '.template.json' over the repo '.json', or None."""
    for name in _OBS_TEMPLATE_NAMES:
        p = os.path.join(obs_dir, name)
        if os.path.isfile(p):
            return p
    return None


def _place_placeholders(names, directory, src_path, *, overwrite):
    """Copy `src_path` onto `directory/<name>` for each name. With `overwrite`
    False an already-present file is left untouched; with True it is replaced.
    Returns the sorted list of names actually written.

    Best-effort: a missing `src_path`, an unusable `directory` or a per-file copy
    error is skipped, never raised. Writes atomically via `.part` plus os.replace,
    and creates `directory`."""
    if not src_path or not os.path.isfile(src_path):
        return []
    try:
        os.makedirs(directory, exist_ok=True)
        have = set(os.listdir(directory))
    except OSError:
        return []
    written = []
    for name in names:
        if name != os.path.basename(name):   # reject path separators
            continue
        if name in have and not overwrite:
            continue
        dst = os.path.join(directory, name)
        tmp = dst + ".part"
        try:
            shutil.copyfile(src_path, tmp)
            os.replace(tmp, dst)
            written.append(name)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass  # temp file already gone or never created
    return sorted(written)


def fill_missing(expected_names, directory, src_path):
    """Copy `src_path` to `directory/<name>` for every name in `expected_names`
    not already present. Returns the sorted list of names actually written.
    Idempotent, best-effort and atomic; see _place_placeholders."""
    return _place_placeholders(expected_names, directory, src_path, overwrite=False)


def reset_placeholders(names, directory, src_path):
    """Copy `src_path` onto `directory/<name>` for every name, REPLACING any
    existing file. Returns the sorted list of names actually written. Used to
    revert an asset to its placeholder when the Sheet no longer links it (#387).
    Best-effort and atomic; see _place_placeholders."""
    return _place_placeholders(names, directory, src_path, overwrite=True)
