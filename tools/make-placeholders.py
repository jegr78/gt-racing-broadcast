#!/usr/bin/env python3
"""Generate the bundled neutral placeholder assets for missing broadcast graphics
and Intro/Outro clips. Maintainer tool, not shipped: run it, commit the output.

Outputs (committed product assets, like src/assets/flags/):
  src/assets/placeholders/transparent-1080p.png   fully transparent 1920x1080 RGBA
  src/assets/placeholders/neutral-5s-1080p.mp4     black, silent, 5 s, 1080p H.264

The PNG is built with the stdlib (zlib); the MP4 needs ffmpeg on PATH.

Usage: python3 tools/make-placeholders.py [--width 1920] [--height 1080] [--seconds 5]
"""
import argparse, os, struct, subprocess, sys, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "src", "assets", "placeholders")


def transparent_png_bytes(width, height):
    """A fully transparent RGBA PNG (alpha 0), encoded with the stdlib."""
    row = b"\x00" * (width * 4)
    raw = bytearray()
    for _ in range(height):
        raw.append(0)            # PNG filter type 0 (None) for the scanline
        raw += row
    comp = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def write_png(path, width, height):
    with open(path, "wb") as fh:
        fh.write(transparent_png_bytes(width, height))
    print(f"OK -> {path} ({os.path.getsize(path)} bytes)")


def write_mp4(path, width, height, seconds):
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           "-i", f"color=c=black:s={width}x{height}:d={seconds}:r=30",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryslow",
           "-crf", "30", "-an", path]
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: ffmpeg not found (brew install ffmpeg).")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: ffmpeg failed: {e}")
    print(f"OK -> {path} ({os.path.getsize(path)} bytes)")


def write_music_loop(path, seconds=24):
    """A seamless, low-volume synthetic ambient loop, royalty-free because it is
    generated. Integer-Hz partials over an integer duration loop click-free."""
    cmd = ["ffmpeg", "-y",
           "-f", "lavfi", "-i", f"sine=frequency=110:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=164:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
           "-filter_complex",
           "[0][1][2]amix=inputs=3:normalize=0,volume=0.10,lowpass=f=800[a]",
           "-map", "[a]", "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", path]
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: ffmpeg not found (brew install ffmpeg).")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: ffmpeg failed: {e}")
    print(f"OK -> {path} ({os.path.getsize(path)} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--seconds", type=int, default=5)
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    write_png(os.path.join(OUT_DIR, "transparent-1080p.png"), a.width, a.height)
    write_mp4(os.path.join(OUT_DIR, "neutral-5s-1080p.mp4"), a.width, a.height, a.seconds)
    write_music_loop(os.path.join(OUT_DIR, "neutral-ambient-loop.mp3"), a.seconds if a.seconds >= 8 else 24)


if __name__ == "__main__":
    main()
