#!/usr/bin/env python3
"""Stdlib unit checks for cookie_jar.py (#615). Run: python3 tests/test_cookie_jar.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "cookie_jar", os.path.join(ROOT, "src", "scripts", "cookie_jar.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# The anonymous jar from the 2026-09-19 incident: three cookies, no login.
ANON_JAR = ("# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-YNID\tv\n"
            ".youtube.com\tTRUE\t/\tFALSE\t0\tPREF\tf6=40000000\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSOCS\tCAI\n")
LOGGED_IN_JAR = ANON_JAR + ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tAFm\n"


def _jar(d, text):
    p = os.path.join(d, "yt-cookies.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def t_text_has_login_needs_a_marker():
    assert m.text_has_login(ANON_JAR) is False
    assert m.text_has_login(LOGGED_IN_JAR) is True
    for marker in m.COOKIE_MARKERS:
        assert m.text_has_login(f".youtube.com\tTRUE\t/\tTRUE\t0\t{marker}\tv\n"), marker
    assert m.text_has_login("") is False and m.text_has_login(None) is False


def t_jar_has_login_reads_the_file():
    with tempfile.TemporaryDirectory() as d:
        assert m.jar_has_login(_jar(d, ANON_JAR)) is False
        assert m.jar_has_login(_jar(d, LOGGED_IN_JAR)) is True


def t_jar_has_login_is_unknown_without_a_jar():
    # No jar is "unknown", not "logged out": the relay must not blame a login it
    # never had a file for.
    assert m.jar_has_login(None) is None
    assert m.jar_has_login("") is None
    with tempfile.TemporaryDirectory() as d:
        assert m.jar_has_login(os.path.join(d, "nope.txt")) is None
        assert m.jar_has_login(d) is None            # a directory: unreadable as a jar


def t_hint_names_the_command():
    assert "racecast cookies <browser>" in m.LOGGED_OUT_HINT


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
