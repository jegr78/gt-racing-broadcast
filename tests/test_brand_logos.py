#!/usr/bin/env python3
"""The timing pages' brand logo resolver (src/scripts/brand_logos.py).
Run: python3 tests/test_brand_logos.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import brand_logos as bl  # noqa: E402

PNG = bl.PNG_MAGIC + b"logo"


def _dirs():
    root = tempfile.mkdtemp()
    over, base = os.path.join(root, "over"), os.path.join(root, "base")
    os.makedirs(over)
    os.makedirs(base)
    return root, over, base


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def t_only_a_plain_key_inside_the_logo_dirs_resolves():
    root, over, base = _dirs()
    _write(os.path.join(root, "secret.png"), PNG)
    _write(os.path.join(base, "bmw.png"), PNG)
    logos = bl.Logos([over, base])
    assert logos.get("bmw") == PNG, "a bundled logo"
    _write(os.path.join(base, "Odd Name.png"), PNG)
    for key in ("../secret", "..", "Odd Name", "bmw.png", "", None, "a" * 65):
        assert logos.get(key) is None, f"{key!r} is no brand key"
    try:
        os.symlink(os.path.join(root, "secret.png"), os.path.join(base, "evil.png"))
    except (OSError, NotImplementedError):
        return                          # no symlinks here (Windows without the privilege)
    assert logos.get("evil") is None, "a link out of the logo dir is never followed"


def t_the_override_wins_and_a_change_is_read_again():
    _root, over, base = _dirs()
    _write(os.path.join(base, "bmw.png"), PNG)
    logos = bl.Logos([over, base])
    assert logos.get("bmw") == PNG
    _write(os.path.join(over, "bmw.png"), PNG + b"-league")
    assert logos.get("bmw") == PNG + b"-league", "the league's override first"
    _write(os.path.join(over, "bmw.png"), PNG + b"-new!!!")
    os.utime(os.path.join(over, "bmw.png"), ns=(1, 1))
    assert logos.get("bmw") == PNG + b"-new!!!", "a replaced file is read again"


def t_for_keys_caps_the_set():
    _root, _over, base = _dirs()
    for i in range(bl.MAX_LOGOS + 5):
        _write(os.path.join(base, f"b{i}.png"), PNG)
    got = bl.Logos([base]).for_keys([f"b{i}" for i in range(bl.MAX_LOGOS + 5)] + ["", "none"])
    assert len(got) == bl.MAX_LOGOS, len(got)


def t_the_byte_window_lets_one_logo_through_and_paces_a_burst():
    now = [0.0]
    w = bl.ByteWindow(1000, window_s=10, clock=lambda: now[0])
    assert w.take(bl.MAX_BYTES), "one logo of any allowed size passes an idle window"
    assert not w.take(1), "the window is spent"
    now[0] = 10.5
    assert w.take(5000) and w.take(5000), "it refills after the window"
    assert not w.take(bl.MAX_BYTES), (
        "a full logo waits once the window holds more than its rate allows")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
