#!/usr/bin/env python3
"""Unit tests for the pure pieces of tools/assets_kit.py (stdlib, no pytest)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import assets_kit as k


MINIMAL = {
    "stills": {"standby": "Standby.png"},
    "scenes": {"intro": {"duration": 30, "fps": 30, "output": "Intro.mp4"}},
}


def _write_kit(dirpath, kit):
    with open(os.path.join(dirpath, "kit.json"), "w", encoding="utf-8") as fh:
        json.dump(kit, fh)
    return dirpath


def t_load_kit_reads_manifest():
    with tempfile.TemporaryDirectory() as d:
        _write_kit(d, MINIMAL)
        kit = k.load_kit(d)
        assert kit["stills"]["standby"] == "Standby.png"
        assert kit["scenes"]["intro"]["fps"] == 30


def t_load_kit_missing_manifest_is_a_clear_error():
    with tempfile.TemporaryDirectory() as d:
        try:
            k.load_kit(d)
        except k.KitError as exc:
            assert "kit.json" in str(exc)
        else:
            raise AssertionError("missing kit.json must raise KitError")


def t_load_kit_rejects_broken_json():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "kit.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        try:
            k.load_kit(d)
        except k.KitError as exc:
            assert "kit.json" in str(exc)
        else:
            raise AssertionError("broken kit.json must raise KitError")


def t_load_kit_requires_stills_or_scenes():
    with tempfile.TemporaryDirectory() as d:
        _write_kit(d, {})
        try:
            k.load_kit(d)
        except k.KitError as exc:
            assert "stills" in str(exc) or "scenes" in str(exc)
        else:
            raise AssertionError("an empty kit must raise KitError")


def t_still_targets_are_sorted_pairs():
    kit = {"stills": {"cover": "Cover.png", "standby": "Standby.png"}}
    assert k.still_targets(kit) == [("cover", "Cover.png"), ("standby", "Standby.png")]


def t_still_targets_can_be_filtered():
    kit = {"stills": {"cover": "Cover.png", "standby": "Standby.png"}}
    assert k.still_targets(kit, only=["standby"]) == [("standby", "Standby.png")]


def t_still_targets_unknown_screen_is_an_error():
    kit = {"stills": {"cover": "Cover.png"}}
    try:
        k.still_targets(kit, only=["nope"])
    except k.KitError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("unknown screen id must raise KitError")


def t_output_name_rejects_path_traversal():
    # kit.json is repo/profile data; a filename must never escape the out dir.
    for bad in ("../evil.png", "sub/dir.png", "/abs.png", "..\\evil.png", ""):
        try:
            k.safe_output_name(bad)
        except k.KitError:
            pass  # rejected, which is what this asserts
        else:
            raise AssertionError(f"{bad!r} must be rejected")


def t_output_name_allows_spaces_and_dashes():
    # The Sheet's Assets tab uses labels like "Standby Cover.png" verbatim.
    assert k.safe_output_name("Standby Cover.png") == "Standby Cover.png"
    assert k.safe_output_name("Post-Race Interviews.png") == "Post-Race Interviews.png"


def t_frame_count_rounds_to_nearest():
    assert k.frame_count(30, 30) == 900
    assert k.frame_count(1.5, 30) == 45
    assert k.frame_count(0.51, 2) == 1


def t_frame_name_is_zero_padded_and_matches_the_ffmpeg_pattern():
    assert k.frame_name(0) == "f00000.jpg"
    assert k.frame_name(1234) == "f01234.jpg"
    assert k.FRAME_PATTERN == "f%05d.jpg"


def t_audio_filter_none_without_fades():
    assert k.audio_filter({}) is None


def t_audio_filter_builds_fade_chain():
    spec = {"fadeIn": 0.8, "fadeOut": {"start": 28.2, "duration": 1.8}}
    assert k.audio_filter(spec) == "afade=t=in:st=0:d=0.8,afade=t=out:st=28.2:d=1.8"


def t_audio_filter_handles_a_single_fade():
    assert k.audio_filter({"fadeIn": 1}) == "afade=t=in:st=0:d=1"
    assert k.audio_filter({"fadeOut": {"start": 5, "duration": 2}}) == \
        "afade=t=out:st=5:d=2"


def t_mux_args_without_audio_have_no_audio_input():
    args = k.mux_args(frames_dir="/f", fps=30, out_path="/o/Intro.mp4",
                      audio_path=None, audio=None)
    assert "-i" in args and os.path.join("/f", k.FRAME_PATTERN) in args
    assert "-c:a" not in args
    assert args[-1] == "/o/Intro.mp4"
    assert "libx264" in args


def t_mux_args_with_audio_seek_before_input():
    # -ss must precede the audio -i, otherwise ffmpeg decodes the whole track.
    args = k.mux_args(frames_dir="/f", fps=30, out_path="/o/Intro.mp4",
                      audio_path="/m/track.mp3",
                      audio={"start": 0.5, "duration": 30, "fadeIn": 0.8})
    i_ss = args.index("-ss")
    i_audio = args.index("/m/track.mp3")
    assert i_ss < i_audio, args
    assert args[i_ss + 1] == "0.5"
    assert "-shortest" in args
    assert "afade=t=in:st=0:d=0.8" in " ".join(args)


def t_mux_args_are_a_list_not_a_shell_string():
    # Every path is a separate argv entry, so a space in a name cannot split it.
    args = k.mux_args(frames_dir="/f", fps=30, out_path="/o/My Video.mp4",
                      audio_path=None, audio=None)
    assert "/o/My Video.mp4" in args


def t_text_config_merges_event_over_kit_defaults():
    kit = {"text": {"title": "KIT", "date": "KITDATE"}}
    event = {"title": "EVENT"}
    assert k.text_config(kit, event, {}) == {"title": "EVENT", "date": "KITDATE"}


def t_text_config_cli_overrides_win():
    kit = {"text": {"title": "KIT"}}
    assert k.text_config(kit, {"title": "EVENT"}, {"title": "CLI"}) == {"title": "CLI"}


def t_text_config_drops_comment_keys():
    # event.json carries "_comment" documentation keys; they must not reach the page.
    assert "_comment" not in k.text_config({}, {"_comment": "doc", "a": 1}, {})


def t_text_config_ignores_empty_cli_values():
    assert k.text_config({"text": {"a": "kit"}}, {}, {"a": None}) == {"a": "kit"}


def t_kit_dir_for_profile():
    p = k.kit_dir("/repo", "erf-wspc")
    assert p == os.path.join("/repo", "profiles", "erf-wspc", "assets-src")


def t_scene_spec_defaults_fps_and_validates():
    kit = {"scenes": {"intro": {"duration": 30, "output": "Intro.mp4"}}}
    spec = k.scene_spec(kit, "intro")
    assert spec["fps"] == k.DEFAULT_FPS
    assert spec["duration"] == 30
    try:
        k.scene_spec(kit, "missing")
    except k.KitError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown scene must raise KitError")


def t_scene_spec_requires_a_duration():
    try:
        k.scene_spec({"scenes": {"x": {"output": "x.mp4"}}}, "x")
    except k.KitError as exc:
        assert "duration" in str(exc)
    else:
        raise AssertionError("a scene without duration must raise KitError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_assets_kit")
