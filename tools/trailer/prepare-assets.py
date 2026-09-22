#!/usr/bin/env python3
"""Assemble the /trailer-assets/ dir the trailer page pulls from (maintainer tool).

``tools/trailer/trailer.html`` references its committed UI shots directly under
``/src/...``, but four league broadcast graphics and one redacted Control-Center
crop are NOT in the repo (league graphics are downloaded per profile; the crop
must have the machine's Tailscale identity removed). This script produces exactly
those five files so ``tools/build-trailer.py --assets-dir`` can serve them:

    Standings.png, Schedule.png, Standby.png, Race Weather 1.png   (copied)
    cc-home-crop.png                                               (generated)

The four graphics come from a profile's runtime graphics dir. Get them with
``racecast --profile <name> graphics`` (writes ``runtime/<name>/graphics/``).
``cc-home-crop.png`` is derived from the committed Control-Center screenshot
``src/docs/slides/assets/img/cc-home.png``: the bottom rows (which show MagicDNS
hostnames) are cropped off and the Tailscale IP is painted over, so the trailer
never leaks tailnet identity.

Usage:
    python3 tools/trailer/prepare-assets.py \\
        --graphics-dir runtime/demo/graphics \\
        --out runtime/trailer/assets
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

# League broadcast graphics the trailer shots use; the names are Sheet labels.
GRAPHICS = ["Standings.png", "Schedule.png", "Standby.png", "Race Weather 1.png"]

CC_HOME_SRC = os.path.join(REPO_ROOT, "src", "docs", "slides", "assets", "img", "cc-home.png")
CC_CROP_HEIGHT = 700                  # keep the top 700 of 900px (drops the MagicDNS rows)
CC_IP_BOX = (553, 176, 675, 206)      # Tailscale IP chip in the Network card
CC_CARD_BG = (27, 35, 54)             # #1B2336, the card background it sits on


def prepare(graphics_dir, out_dir):
    from PIL import Image, ImageDraw

    os.makedirs(out_dir, exist_ok=True)

    missing = [g for g in GRAPHICS if not os.path.isfile(os.path.join(graphics_dir, g))]
    if missing:
        sys.exit(f"missing league graphics in {graphics_dir}: {missing}\n"
                 f"run `racecast --profile <name> graphics` first")
    for g in GRAPHICS:
        shutil.copy2(os.path.join(graphics_dir, g), os.path.join(out_dir, g))
        print(f"copied  {g}")

    im = Image.open(CC_HOME_SRC).convert("RGB")
    im = im.crop((0, 0, im.width, min(CC_CROP_HEIGHT, im.height)))
    ImageDraw.Draw(im).rectangle(list(CC_IP_BOX), fill=CC_CARD_BG)
    crop_path = os.path.join(out_dir, "cc-home-crop.png")
    im.save(crop_path)
    print(f"generated  cc-home-crop.png (cropped + IP redacted) -> {crop_path}")

    print(f"\nassets ready in {out_dir}. Now supply --music and run tools/build-trailer.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graphics-dir", required=True,
                    help="a profile's runtime graphics dir (e.g. runtime/demo/graphics)")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "runtime", "trailer", "assets"))
    args = ap.parse_args()
    prepare(os.path.abspath(args.graphics_dir), os.path.abspath(args.out))


if __name__ == "__main__":
    main()
