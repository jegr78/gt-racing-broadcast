#!/usr/bin/env python3
"""Stands in for ffmpeg under the stream stub's tests: echoes its argv and label, then
writes PLAY or FREEZE chunks to stdout until the pipe breaks."""
import os
import sys
import threading

args = sys.argv[1:]
if args[:1] == ["--fail-drawtext"]:
    args = args[1:]
    if any("drawtext" in a for a in args):
        sys.stderr.write("No such filter: 'drawtext'\n")
        sys.exit(1)
stub_dir = os.path.dirname(os.path.dirname(os.getcwd()))
with open(os.path.join(stub_dir, "fake-ffmpeg.pid"), "w") as fh:
    fh.write(str(os.getpid()))
label = ""
if os.path.exists("label.txt"):
    with open("label.txt", encoding="utf-8") as fh:
        label = fh.read()
out = sys.stdout.buffer
chunk = (b"FREEZE" if any("smptebars" in a for a in args) else b"PLAY") * 500
tick = threading.Event()
try:
    out.write(("ARGV " + " ".join(args) + " LABEL[" + label + "] ARGV-END\n").encode("utf-8"))
    while True:
        out.write(chunk)
        out.flush()
        tick.wait(0.05)
except OSError:
    sys.exit(0)
