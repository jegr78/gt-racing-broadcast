#!/usr/bin/env python3
"""Stdlib unit checks for get-graphics.py. Run: python3 tests/test_graphics.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "getgraphics", os.path.join(ROOT, "src", "relay", "get-graphics.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_drive_id_file_form():
    assert m.drive_id("https://drive.google.com/file/d/ABC_123-x/view?usp=sharing") == "ABC_123-x"


def t_drive_id_id_form():
    assert m.drive_id("https://drive.google.com/uc?export=download&id=ZZ9_y") == "ZZ9_y"


def t_drive_id_none():
    assert m.drive_id("https://youtu.be/AAA") is None
    assert m.drive_id("") is None


def t_to_download_url():
    assert m.to_download_url("XYZ") == "https://drive.google.com/uc?export=download&id=XYZ"


def t_safe_filename_basic():
    assert m.safe_filename("Race Results") == "Race Results.png"
    assert m.safe_filename("  Standings ") == "Standings.png"


def t_safe_filename_rejects():
    assert m.safe_filename("") is None
    assert m.safe_filename("a/b") is None
    assert m.safe_filename("a\\b") is None
    assert m.safe_filename("bad\x01") is None


def t_graphics_from_csv_picks_drive_skips_youtube():
    rows = [["Intro Video", "https://youtu.be/AAA"],
            ["Standings", "https://drive.google.com/file/d/SID/view?usp=sharing"],
            ["Schedule", "https://drive.google.com/file/d/SCH/view"]]
    assert m.graphics_from_csv(rows) == {
        "Standings": "https://drive.google.com/file/d/SID/view?usp=sharing",
        "Schedule": "https://drive.google.com/file/d/SCH/view"}, m.graphics_from_csv(rows)


def t_graphics_from_csv_excludes_media_labels():
    # get-media.py owns these rows. A Drive-hosted Intermission Music MP3 grabbed
    # here would be saved as 'Intermission Music.png' and fail the PNG signature
    # check. Intro/Outro escape only when YouTube-hosted. (#368)
    rows = [["Intermission Music", "https://drive.google.com/file/d/MUS/view"],
            ["Intro Video", "https://drive.google.com/file/d/INTRO/view"],
            ["Outro Video", "https://drive.google.com/file/d/OUTRO/view"],
            ["Intermission", "https://drive.google.com/file/d/BG/view"]]
    assert m.graphics_from_csv(rows) == {
        "Intermission": "https://drive.google.com/file/d/BG/view"}, m.graphics_from_csv(rows)


def t_graphics_from_csv_label_verbatim_and_empty():
    rows = [["Race Weather 1", "https://drive.google.com/file/d/W1/view"],
            ["", "https://drive.google.com/file/d/X/view"],
            ["NoUrl", ""]]
    assert m.graphics_from_csv(rows) == {
        "Race Weather 1": "https://drive.google.com/file/d/W1/view"}


def t_unlinked_targets_are_expected_minus_linked():
    # The Sheet links Overlay and Standings, while the OBS collection also expects
    # the three weather overlays. An unlinked one resets to the placeholder.
    expected = ["Overlay.png", "Standings.png", "Race Weather 1.png",
                "Race Weather 2.png", "Quali Weather.png"]
    linked = {"Overlay": "u1", "Standings": "u2"}
    assert m.unlinked_graphic_targets(expected, linked) == [
        "Quali Weather.png", "Race Weather 1.png", "Race Weather 2.png"]


def t_unlinked_targets_empty_when_all_linked():
    expected = ["Overlay.png", "Standings.png"]
    linked = {"Overlay": "u1", "Standings": "u2"}
    assert m.unlinked_graphic_targets(expected, linked) == []


def t_unlinked_targets_scoped_to_only():
    # A --only run must not reset graphics outside the requested label set.
    expected = ["Overlay.png", "Standings.png", "Race Weather 1.png"]
    linked = {"Overlay": "u1"}   # Standings + Race Weather 1 are unlinked
    assert m.unlinked_graphic_targets(expected, linked, {"Standings"}) == ["Standings.png"]


def t_graphics_dir_repo():
    # Built with os.path.join because the separator differs on Windows.
    got = m.graphics_dir(os.path.join("/x", "src", "relay"))
    assert got == os.path.join("/x", "runtime", "graphics"), got


def t_graphics_dir_pkg():
    got = m.graphics_dir(os.path.join("/x/GT_Racecast_Package", "relay"))
    assert got == os.path.join("/x/GT_Racecast_Package", "graphics"), got


def t_internal_from_csv_checkbox_true():
    rows = [["Name", "Link", "Internal"],
            ["Standings", "https://drive.google.com/file/d/S/view", "FALSE"],
            ["Standby", "https://drive.google.com/file/d/B/view", "TRUE"],
            ["Weather Rain", "", "TRUE"]]   # ticked, no link -> still internal
    assert m.internal_from_csv(rows) == {"Standby", "Weather Rain"}, m.internal_from_csv(rows)


def t_internal_from_csv_various_truthy_and_header_aliases():
    rows = [["Label", "Link", "OBS only"],
            ["A", "x", "x"], ["B", "x", "✓"], ["C", "x", "yes"],
            ["D", "x", ""], ["E", "x", "false"]]
    assert m.internal_from_csv(rows) == {"A", "B", "C"}, m.internal_from_csv(rows)


def t_internal_from_csv_no_header_or_no_column_is_empty():
    assert m.internal_from_csv([["Standings", "https://drive.google.com/file/d/S/view"]]) == set(), \
        "a header-less sheet has no Internal column"
    # Header present, Internal column absent.
    assert m.internal_from_csv([["Name", "Link"], ["Standings", "x"]]) == set()
    assert m.internal_from_csv([]) == set()


def t_write_manifest_shape():
    import json as _json, tempfile, os as _os
    with tempfile.TemporaryDirectory() as d:
        m.write_manifest(d, {"Standby", "Weather Rain"})
        with open(_os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
            data = _json.load(fh)
        assert data == {"internal": ["Standby", "Weather Rain"]}, data  # sorted


def t_graphics_from_csv_ignores_header_row():
    # A header row must not become a graphic: "Link" is not a Drive URL.
    rows = [["Name", "Link", "Internal"],
            ["Standings", "https://drive.google.com/file/d/S/view", "FALSE"]]
    assert m.graphics_from_csv(rows) == {
        "Standings": "https://drive.google.com/file/d/S/view"}, m.graphics_from_csv(rows)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("ALL PASS")
