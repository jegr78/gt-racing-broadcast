#!/usr/bin/env python3
"""Unit tests for the pure pieces of tools/walkthrough_core.py (stdlib, no pytest).

The heavy render path (Playwright + Piper + ffmpeg) is local-only and never runs
in CI; only the import-safe pure helpers below are exercised here.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import walkthrough_core as w


def t_notes_from_markdown_extracts_after_separator():
    tpl = "## Title\nSome slide body.\nNote: This is the spoken script."
    assert w.notes_from_markdown(tpl) == "This is the spoken script."


def t_notes_from_markdown_is_case_insensitive_and_plural():
    assert w.notes_from_markdown("body\nnotes: hi").strip() == "hi"
    assert w.notes_from_markdown("body\nNOTE: hi").strip() == "hi"


def t_notes_from_markdown_keeps_multiline_note():
    tpl = "body\nNote: line one.\nline two."
    assert w.notes_from_markdown(tpl) == "line one.\nline two."


def t_notes_from_markdown_empty_when_absent():
    assert w.notes_from_markdown("## Title\njust a slide, no script") == ""


def t_notes_from_markdown_splits_only_on_first():
    # A later literal "note:" inside the spoken text must survive.
    tpl = "body\nNote: remember to note: the stream key."
    assert w.notes_from_markdown(tpl) == "remember to note: the stream key."


def t_sanitize_unwraps_markdown_link_to_text():
    assert w.sanitize_note_text("See [the wiki](https://x/y) now") == "See the wiki now"


def t_sanitize_strips_emphasis_and_code():
    assert w.sanitize_note_text("the **final-part** `relay` only") == "the final-part relay only"


def t_sanitize_drops_html_tags():
    assert w.sanitize_note_text('<span class="role-tag">Tag</span> Producer') == "Tag Producer"


def t_sanitize_collapses_whitespace():
    assert w.sanitize_note_text("line one.\n   line two.\n") == "line one. line two."


def t_sanitize_empty_stays_empty():
    assert w.sanitize_note_text("   \n  ") == ""


def t_frame_lock_rounds_up_to_whole_frames():
    # 1.00s at 30fps is already frame-aligned, so it is unchanged.
    assert w.frame_lock(1.0, 30) == 1.0
    # 1.01s gives 31 frames, so 31/30: it rounds up so no speech is clipped.
    assert abs(w.frame_lock(1.01, 30) - 31 / 30) < 1e-9


def t_frame_lock_minimum_one_frame():
    # zero-length audio still yields a single frame, never a 0-length segment
    assert w.frame_lock(0.0, 30) == 1 / 30


def t_ffmpeg_slideshow_cmd_single_pass_concat():
    slides = [("a.png", "a.mp3", 2.0), ("b.png", "b.mp3", 3.0)]
    cmd = w.ffmpeg_slideshow_cmd(slides, "out.mp4", width=1920, height=1080, fps=30)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4"
    # each slide contributes a frame-locked image input, -loop 1 -t T -i img, plus audio
    assert cmd.count("-loop") == 2
    for f in ("a.png", "a.mp3", "b.png", "b.mp3"):
        assert f in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    # single pass: scale and fps per video, apad and atrim per audio, one concat node
    assert "scale=1920:1080" in fc and "fps=30" in fc
    assert "apad" in fc and "atrim=0:2.000000" in fc
    assert "concat=n=2:v=1:a=1[v][a]" in fc
    # the mapped, re-encoded single timeline, with no -c copy stream stitching
    assert "[v]" in cmd and "[a]" in cmd
    assert "libx264" in cmd and "yuv420p" in cmd and "-c:a" in cmd
    assert "-shortest" not in cmd


def t_output_mp4_name_from_deck():
    assert w.output_mp4_name("producer.html") == "producer.mp4"
    assert w.output_mp4_name("race-control") == "race-control.mp4"


def t_thumbnail_name_from_deck():
    assert w.thumbnail_name("producer.html") == "producer-thumb.png"
    assert w.thumbnail_name("race-control") == "race-control-thumb.png"


# Gcloud TTS request building.

def t_gcloud_language_code_from_voice():
    assert w.gcloud_language_code("en-US-Neural2-J") == "en-US"
    assert w.gcloud_language_code("en-GB-Studio-B") == "en-GB"


def t_gcloud_tts_request_shape():
    body = w.gcloud_tts_request("Hello there.", "en-US-Neural2-J")
    assert body["input"] == {"text": "Hello there."}
    assert body["voice"] == {"languageCode": "en-US", "name": "en-US-Neural2-J"}
    # the default encoding is MP3, which is self-describing and ffmpeg-friendly
    assert body["audioConfig"]["audioEncoding"] == "MP3"


def t_gcloud_tts_request_optional_prosody():
    body = w.gcloud_tts_request("Hi.", "en-US-Neural2-J", speaking_rate=0.95)
    assert body["audioConfig"]["speakingRate"] == 0.95
    # pitch omitted when not given
    assert "pitch" not in body["audioConfig"]


def t_gcloud_tts_url_carries_key_and_path():
    url = w.gcloud_tts_url("SECRET123")
    assert url.startswith("https://texttospeech.googleapis.com/v1/text:synthesize")
    assert "key=SECRET123" in url


def t_split_sentences_keeps_terminators():
    assert w.split_sentences("Go live. Then wait! Ready?") == \
        ["Go live.", "Then wait!", "Ready?"]


def t_split_sentences_trailing_fragment_without_punctuation():
    assert w.split_sentences("just one line") == ["just one line"]


def t_split_sentences_empty():
    assert w.split_sentences("   ") == []


# Captions: caption_cues, absolute timing.

def t_caption_cues_one_segment_one_sentence():
    cues = w.caption_cues([("Hello there.", 4.0)])
    assert cues == [(0.0, 4.0, "Hello there.")]


def t_caption_cues_splits_segment_time_by_sentence_length():
    # two equal-length sentences share a 10s segment, so 5s each, back to back
    cues = w.caption_cues([("AAAA. BBBB.", 10.0)])
    assert len(cues) == 2
    assert cues[0] == (0.0, 5.0, "AAAA.")
    assert cues[1] == (5.0, 10.0, "BBBB.")


def t_caption_cues_accumulates_across_segments():
    cues = w.caption_cues([("One.", 3.0), ("Two.", 2.0)])
    assert cues[0] == (0.0, 3.0, "One.")
    assert cues[1] == (3.0, 5.0, "Two.")


def t_format_ts_srt_and_vtt():
    assert w.format_ts(3661.5, ",") == "01:01:01,500"
    assert w.format_ts(3661.5, ".") == "01:01:01.500"
    assert w.format_ts(0.0, ",") == "00:00:00,000"


def t_to_srt_numbered_blocks():
    srt = w.to_srt([(0.0, 2.0, "Hi."), (2.0, 4.0, "Bye.")])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:02,000\nHi.\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nBye.\n\n")


def t_to_vtt_has_header_and_dot_timestamps():
    vtt = w.to_vtt([(0.0, 2.0, "Hi.")])
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:02.000\nHi." in vtt


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_walkthrough")
