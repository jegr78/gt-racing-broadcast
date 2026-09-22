#!/usr/bin/env python3
"""Stdlib checks for the standalone-binary builder's pure decision helpers.
Run: python3 tests/test_build_binary.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "build_binary", os.path.join(ROOT, "tools", "build-binary.py"))
bb = importlib.util.module_from_spec(spec); spec.loader.exec_module(bb)


def t_icon_macos_uses_icns():
    arg = bb._icon_arg(platform="darwin", osname="posix", exists=lambda p: True)
    assert arg[0] == "--icon" and arg[1].endswith(os.path.join("assets", "app-icon.icns"))


def t_icon_windows_uses_ico():
    arg = bb._icon_arg(platform="win32", osname="nt", exists=lambda p: True)
    assert arg[0] == "--icon" and arg[1].endswith(os.path.join("assets", "app-icon.ico"))


def t_icon_linux_has_none():
    # PyInstaller cannot embed an icon into an ELF, so Linux gets no --icon.
    assert bb._icon_arg(platform="linux", osname="posix", exists=lambda p: True) == []


def t_icon_missing_file_is_skipped():
    # A missing committed icon must not break the build (just no icon).
    assert bb._icon_arg(platform="darwin", osname="posix", exists=lambda p: False) == []
    assert bb._icon_arg(platform="win32", osname="nt", exists=lambda p: False) == []


def t_every_served_html_dir_is_bundled():
    # Each src/ subdir that holds a relay/Control-Center-served .html page must be
    # listed in DATA, or the frozen binary 404s that page: its here-relative lookup
    # finds nothing under _MEIPASS/src/.
    src = os.path.join(ROOT, "src")
    served = set()
    for dirpath, _dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        if rel == "." or rel.split(os.sep)[0] == "docs":
            continue
        if any(f.endswith(".html") for f in files):
            served.add(rel.split(os.sep)[0])
    missing = served - set(bb.DATA)
    assert not missing, f"src dirs with served HTML not bundled in DATA: {sorted(missing)}"


def t_committed_icons_exist_and_are_valid():
    # A release build embeds an icon only if the committed files are present and
    # well-formed. Regenerate them from the SVG with tools/make-icons.py.
    import struct
    icns = os.path.join(ROOT, "src", "assets", "app-icon.icns")
    ico = os.path.join(ROOT, "src", "assets", "app-icon.ico")
    assert os.path.isfile(icns) and os.path.isfile(ico)
    with open(icns, "rb") as fh:
        assert fh.read(4) == b"icns"                      # ICNS magic
    with open(ico, "rb") as fh:
        head = fh.read(6)
    reserved, kind, count = struct.unpack("<HHH", head)
    assert reserved == 0 and kind == 1 and count >= 1     # ICONDIR: type 1 = icon


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
