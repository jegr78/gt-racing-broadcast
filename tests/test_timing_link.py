#!/usr/bin/env python3
"""The served path stored beside the public timing link (#1161).
Run: python3 tests/test_timing_link.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import timing_link  # noqa: E402


def t_timing_link_served_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = timing_link.path_in(td)
        timing_link.create(p)
        assert timing_link.served(p) is None, "unset until the producer switches"
        timing_link.set_served(p, "funnel")
        assert timing_link.served(p) == "funnel"


def t_renewal_keeps_the_served_path_and_clear_drops_it():
    with tempfile.TemporaryDirectory() as td:
        p = timing_link.path_in(td)
        timing_link.create(p)
        timing_link.set_served(p, "funnel")
        timing_link.create(p)
        assert timing_link.served(p) == "funnel", "a renewed link keeps the producer's choice"
        timing_link.clear(p)
        assert timing_link.served(p) is None, "event stop ends the choice with the link"


def t_set_served_rejects_unknown_values():
    with tempfile.TemporaryDirectory() as td:
        p = timing_link.path_in(td)
        try:
            timing_link.set_served(p, "cdn")
        except ValueError:
            return
        raise AssertionError("only hosted and funnel are paths")


def t_served_ignores_a_corrupt_file():
    with tempfile.TemporaryDirectory() as td:
        p = timing_link.path_in(td)
        with open(os.path.join(td, timing_link.SERVED_FILENAME), "w", encoding="utf-8") as fh:
            fh.write('{"served": "elsewhere"}')
        assert timing_link.served(p) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
