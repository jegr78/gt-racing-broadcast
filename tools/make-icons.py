#!/usr/bin/env python3
"""Maintainer-only: regenerate the platform app icons from src/assets/app-icon.svg.

The binaries embed a native icon via PyInstaller --icon (build-binary.py):
macOS wants .icns, Windows wants .ico, and Linux cannot embed one into an ELF.
No CI runner is guaranteed to have Pillow, cairosvg or ImageMagick, so both
formats are generated once here and committed next to the SVG, which stays the
single source of truth (it is also the Control Center favicon, #57).
Re-run this whenever app-icon.svg changes:  python3 tools/make-icons.py

macOS-only: it uses qlmanage, sips and iconutil. The .ico is assembled with a
stdlib packer, so no third-party imaging dependency is introduced. (#58)
"""
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "src", "assets")
SVG = os.path.join(ASSETS, "app-icon.svg")
MASTER = 1024                       # render once at this size, then downscale
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# (iconset filename, pixel size), the names Apple's iconutil expects.
ICONSET = (("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
           ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
           ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
           ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
           ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024))


def _require_macos():
    if sys.platform != "darwin":
        sys.exit("make-icons.py is macOS-only (needs qlmanage/sips/iconutil); "
                 "the generated .icns/.ico are committed, so CI never runs this.")


def render_master(workdir):
    """Rasterize the SVG to a MASTER x MASTER PNG via qlmanage. qlmanage will not
    upscale past the SVG's intrinsic size, so stamp width/height to the target
    first; a viewBox-only SVG renders the background rect blank here."""
    sized = os.path.join(workdir, "sized.svg")
    with open(SVG, encoding="utf-8") as fh:
        svg = fh.read()
    svg = svg.replace('width="64" height="64"', f'width="{MASTER}" height="{MASTER}"')
    if f'width="{MASTER}"' not in svg:
        sys.exit("could not stamp width/height on the SVG. Has app-icon.svg changed?")
    with open(sized, "w", encoding="utf-8") as fh:
        fh.write(svg)
    subprocess.run(["qlmanage", "-t", "-s", str(MASTER), "-o", workdir, sized],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    master = os.path.join(workdir, "sized.svg.png")
    if not os.path.isfile(master):
        sys.exit("qlmanage produced no PNG, cannot build icons.")
    return master


def scale(master, size, out):
    """Downscale the master PNG to `size`x`size` with sips."""
    subprocess.run(["sips", "-z", str(size), str(size), master, "--out", out],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def build_icns(master, workdir):
    iconset = os.path.join(workdir, "app-icon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for name, size in ICONSET:
        scale(master, size, os.path.join(iconset, name))
    out = os.path.join(ASSETS, "app-icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    return out


def build_ico(master, workdir):
    """Pack PNG-compressed images into a single .ico; Vista and later read PNG
    entries. The format is a 6-byte ICONDIR, one 16-byte ICONDIRENTRY per image,
    then the PNG payloads. A size of 256 is encoded as 0 in the 1-byte
    width/height."""
    images = []
    for size in ICO_SIZES:
        png = scale(master, size, os.path.join(workdir, f"ico-{size}.png"))
        with open(png, "rb") as fh:
            images.append((size, fh.read()))
    out = os.path.join(ASSETS, "app-icon.ico")
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)          # reserved, type=icon, count
    offset = 6 + 16 * count
    entries, payloads = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size               # 256 -> 0 per the spec
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        payloads += data
        offset += len(data)
    with open(out, "wb") as fh:
        fh.write(header + entries + payloads)
    return out


def main():
    _require_macos()
    if not os.path.isfile(SVG):
        sys.exit(f"missing {SVG}")
    workdir = tempfile.mkdtemp(prefix="racecast-icons-")
    master = render_master(workdir)
    icns = build_icns(master, workdir)
    ico = build_ico(master, workdir)
    print(f"wrote {os.path.relpath(icns, ROOT)} ({os.path.getsize(icns)} bytes)")
    print(f"wrote {os.path.relpath(ico, ROOT)} ({os.path.getsize(ico)} bytes)")
    print("commit both: build-binary.py embeds them via PyInstaller --icon.")


if __name__ == "__main__":
    main()
