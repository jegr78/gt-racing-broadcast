#!/usr/bin/env python3
"""Pure, import-testable helpers for the narrated-walkthrough video builder.

Notes extraction, spoken-text sanitisation, the per-slide ffmpeg command, the
concat list and the output naming. Unit-tested in tests/test_walkthrough.py.

The lifecycle (headless browser screenshots, TTS, ffmpeg execution) lives in
tools/build-walkthrough-videos.py and is local-only, never CI. Spec:
docs/superpowers/specs/2026-06-22-walkthrough-videos-design.md.
"""
import math
import os
import re

# Reveal's markdown plugin splits speaker notes off the slide body on a
# `Note:`/`Notes:` separator (default `notesSeparator: 'notes?:'`, matched
# case-insensitively). Mirroring that first-split semantics is what makes a
# no-browser `--list` agree with what Reveal renders into <aside class="notes">.
_NOTES_RE = re.compile(r"notes?:", re.IGNORECASE)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # [text](url) -> text
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")    # ![alt](url) -> (dropped)
_HTML_TAG_RE = re.compile(r"<[^>]+>")                  # <span ...> -> (dropped)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`)")         # bold/italic/code markers
_WS_RE = re.compile(r"\s+")


def notes_from_markdown(template):
    """Return the spoken-note text from one Reveal markdown template, or "".

    Everything after the first `Note:`/`Notes:` separator is the note (matching
    Reveal's first-split behaviour); a later literal "note:" inside the spoken
    text is preserved.
    """
    parts = _NOTES_RE.split(template, maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def sanitize_note_text(raw):
    """Turn an authored markdown note into clean text for a TTS engine.

    Drops images, unwraps links to their text, strips emphasis/code markers and
    raw HTML tags, and collapses all whitespace (incl. newlines) to single spaces.
    The live render path reads DOM innerText, which is already plain; notes
    authored with markdown come through here.
    """
    s = _MD_IMAGE_RE.sub("", raw)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _HTML_TAG_RE.sub("", s)
    s = _EMPHASIS_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def frame_lock(duration, fps=30):
    """Round an audio duration UP to a whole number of video frames.

    If a slide's video and audio differ in length, a concatenation sums each track
    independently and the A/V gap grows every slide. An exact frame count per
    segment means video == audio. Rounding UP keeps the apad-padded audio from
    being clipped mid-word.
    """
    frames = max(1, math.ceil(duration * fps))
    return frames / fps


def ffmpeg_slideshow_cmd(slides, out, width=1920, height=1080, fps=30):
    """Build a SINGLE-PASS ffmpeg argv that renders all slides into one MP4.

    `slides` is a list of (image, audio, seconds) where `seconds` is frame-locked
    (see frame_lock). Each image is looped to exactly that length and each audio
    is padded and trimmed to match, then everything is concatenated with the
    `concat` filter onto one continuous, re-encoded timeline, so no per-segment
    A/V mismatch and no copy-concat priming gap can accumulate.
    """
    args = ["ffmpeg", "-y"]
    for img, aud, t in slides:
        args += ["-loop", "1", "-t", f"{t:.6f}", "-i", img, "-i", aud]
    chains, labels = [], []
    for i, (_img, _aud, t) in enumerate(slides):
        vi, ai = 2 * i, 2 * i + 1
        chains.append(f"[{vi}:v]scale={width}:{height},setsar=1,fps={fps}[v{i}]")
        chains.append(
            f"[{ai}:a]apad,atrim=0:{t:.6f},asetpts=N/SR/TB[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    n = len(slides)
    fc = ";".join(chains) + ";" + "".join(labels) + \
        f"concat=n={n}:v=1:a=1[v][a]"
    args += [
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k",
        out,
    ]
    return args


def output_mp4_name(deck):
    """`producer.html` / `producer` -> `producer.mp4`."""
    return os.path.splitext(os.path.basename(deck))[0] + ".mp4"


def thumbnail_name(deck):
    """`producer.html` / `producer` -> `producer-thumb.png` (YouTube thumbnail)."""
    return os.path.splitext(os.path.basename(deck))[0] + "-thumb.png"


# Google Cloud Text-to-Speech (REST), request building only. The HTTP call and
# base64 decode live in the tool and go through src/scripts/http_util.py (the
# User-Agent rule). The API key is a machine-local secret (env
# RACECAST_GCLOUD_TTS_KEY) and never lands here.

GCLOUD_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"


def gcloud_language_code(voice):
    """`en-US-Neural2-J` -> `en-US` (first two BCP-47 segments)."""
    return "-".join(voice.split("-")[:2])


def gcloud_tts_request(text, voice, audio_encoding="MP3",
                       speaking_rate=None, pitch=None):
    """Build the JSON body for a text:synthesize call.

    MP3 by default, because it is self-describing and ffmpeg reads it without a
    sample-rate hint, unlike headerless LINEAR16. speaking_rate and pitch are
    only included when given.
    """
    audio = {"audioEncoding": audio_encoding}
    if speaking_rate is not None:
        audio["speakingRate"] = speaking_rate
    if pitch is not None:
        audio["pitch"] = pitch
    return {
        "input": {"text": text},
        "voice": {"languageCode": gcloud_language_code(voice), "name": voice},
        "audioConfig": audio,
    }


def gcloud_tts_url(key):
    """Endpoint with the API key as a query param."""
    return f"{GCLOUD_TTS_ENDPOINT}?key={key}"


# Captions (SRT / WebVTT) are built from the exact spoken text and the per-slide
# frame-locked durations, so they line up with the audio without speech recognition.

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")


def split_sentences(text):
    """Split spoken text into sentence-ish caption lines (keeps terminators)."""
    return [m.group().strip() for m in _SENTENCE_RE.finditer(text)
            if m.group().strip()]


def caption_cues(segments):
    """Turn [(spoken_text, seconds), ...] into absolute (start, end, text) cues.

    Each segment's time is split across its sentences in proportion to their
    length, and cues accumulate on one global clock, so the captions track the
    narration across slides and the intro/outro without drift.
    """
    cues = []
    clock = 0.0
    for text, seconds in segments:
        sentences = split_sentences(text)
        if not sentences:
            clock += seconds
            continue
        total = sum(len(s) for s in sentences)
        for s in sentences:
            dur = seconds * len(s) / total
            cues.append((clock, clock + dur, s))
            clock += dur
    return cues


def format_ts(seconds, decimal_sep):
    """Seconds -> 'HH:MM:SS<sep>mmm' (sep ',' for SRT, '.' for VTT)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{decimal_sep}{ms:03d}"


def to_srt(cues):
    """Format cues as an SRT subtitle document."""
    out = []
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(f"{i}\n{format_ts(start, ',')} --> {format_ts(end, ',')}\n"
                   f"{text}\n\n")
    return "".join(out)


def to_vtt(cues):
    """Format cues as a WebVTT subtitle document."""
    out = ["WEBVTT\n\n"]
    for start, end, text in cues:
        out.append(f"{format_ts(start, '.')} --> {format_ts(end, '.')}\n"
                   f"{text}\n\n")
    return "".join(out)
