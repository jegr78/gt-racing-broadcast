#!/usr/bin/env python3
"""Checks for tools/strip_companion_pass.py, the Companion export -> repo importer.
Run: python3 tests/test_strip_companion_pass.py"""
import gzip, importlib.util, json, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "src", "companion", "racecast-buttons.companionconfig")
_spec = importlib.util.spec_from_file_location(
    "strip_companion_pass", os.path.join(ROOT, "tools", "strip_companion_pass.py"))
strip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(strip)


def _run(src_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.companionconfig"), os.path.join(tmp, "out.companionconfig")
        with open(src, "wb") as fh:
            fh.write(src_bytes)
        strip.main(src, dst)
        with open(dst, "rb") as fh:
            return fh.read()


def t_committed_config_round_trips_byte_identical():
    # Re-stripping the committed config must reproduce it exactly: LF endings on
    # every OS and the trailing newline, so an import never shows up as a whole-file
    # diff. The checkout itself may be CRLF (autocrlf on Windows), hence the normalise.
    with open(CONFIG, "rb") as fh:
        committed = fh.read().replace(b"\r\n", b"\n")
    out = _run(committed)
    assert b"\r" not in out, "strip must write LF line endings"
    assert out.endswith(b"}\n"), out[-20:]
    assert out == committed


def t_blanks_password_in_gzipped_export():
    cfg = {"instances": {"obs": {"config": {"host": "127.0.0.1", "pass": "hunter2"}}}}
    out = json.loads(_run(gzip.compress(json.dumps(cfg).encode())))
    assert out["instances"]["obs"]["config"] == {"host": "127.0.0.1", "pass": ""}, out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
