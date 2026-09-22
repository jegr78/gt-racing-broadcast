#!/usr/bin/env python3
"""Stdlib unit checks for get-brands.py. Run: python3 tests/test_brands.py"""
import importlib.util, inspect, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


m = _load("getbrands", os.path.join("src", "relay", "get-brands.py"))
feeds = _load("irofeeds", os.path.join("src", "relay", "racecast-feeds.py"))


def t_asset_key_matches_brand_text():
    # The downloaded filename stem must equal the HUD's brandKey for that brand.
    assert m.asset_key("BMW") == "bmw"
    assert m.asset_key("Aston Martin") == "aston-martin"
    assert m.asset_key("  Cupra ") == "cupra"
    assert m.asset_key("") == ""


def t_asset_key_pinned_to_relay():
    """Drift guard: the duplicated asset_key must stay byte-identical to the relay's."""
    norm = lambda fn: inspect.getsource(fn).strip()
    assert norm(m.asset_key) == norm(feeds.asset_key)


def t_safe_filename():
    assert m.safe_filename("bmw") == "bmw.png"
    assert m.safe_filename("aston-martin") == "aston-martin.png"
    assert m.safe_filename("") is None
    assert m.safe_filename("a/b") is None
    assert m.safe_filename("../x") is None
    assert m.safe_filename("BMW") is None   # already normalized; uppercase is not a valid key


def t_brands_from_csv_normalizes_key_and_picks_drive():
    rows = [["Brand", "Logo"],
            ["BMW", "https://drive.google.com/file/d/B1/view?usp=sharing"],
            ["Aston Martin", "https://drive.google.com/file/d/A2/view"],
            ["YouTubeRow", "https://youtu.be/AAA"],
            ["", "https://drive.google.com/file/d/X/view"]]
    assert m.brands_from_csv(rows) == {
        "bmw": "https://drive.google.com/file/d/B1/view?usp=sharing",
        "aston-martin": "https://drive.google.com/file/d/A2/view"}, m.brands_from_csv(rows)


def t_brands_from_csv_header_variants():
    # "Brand Key" header is accepted too; logo header may be "Logo URL".
    rows = [["Brand Key", "Logo URL"],
            ["Cupra", "https://drive.google.com/file/d/C/view"]]
    assert m.brands_from_csv(rows) == {"cupra": "https://drive.google.com/file/d/C/view"}


def t_brands_from_csv_no_header_returns_empty():
    rows = [["Something", "Else"], ["x", "y"]]
    assert m.brands_from_csv(rows) == {}


def t_brands_dir_repo():
    got = m.brands_dir(os.path.join("/x", "src", "relay"))
    assert got == os.path.join("/x", "runtime", "brands"), got


def t_brands_dir_pkg():
    got = m.brands_dir(os.path.join("/x/GT_Racecast_Package", "relay"))
    assert got == os.path.join("/x/GT_Racecast_Package", "brands"), got


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("ALL PASS")
