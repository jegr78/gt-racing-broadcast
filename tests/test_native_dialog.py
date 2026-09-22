#!/usr/bin/env python3
"""native_dialog unit checks (pure command builders + dispatch). Run: python3 tests/test_native_dialog.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "scripts"))
import native_dialog as nd


def t_osascript_argv_quotes_and_titles():
    argv = nd.osascript_argv('port 8089 in use "now"')
    assert argv[0] == "osascript"
    joined = " ".join(argv)
    assert "racecast Control Center" in joined
    assert '"now"' not in joined, \
        "double quotes are neutralised so the AppleScript string cannot break out"


def t_notify_darwin_runs_osascript():
    calls = []
    nd.notify("boom", platform="darwin", run=calls.append)
    assert calls and calls[0][0] == "osascript"


def t_notify_windows_calls_msgbox():
    calls = []
    nd.notify("boom", platform="win32", run=lambda a: None,
              msgbox=calls.append)
    assert calls == ["boom"]


def t_notify_linux_falls_back_to_stderr():
    # neither run nor msgbox is invoked on linux; the message goes to stderr
    ran = []
    nd.notify("boom", platform="linux", run=ran.append,
              msgbox=ran.append)
    assert ran == []


def _run_all():
    fns = sorted(n for n in globals() if n.startswith("t_"))
    for n in fns:
        globals()[n]()
        print(f"  ok {n}")
    print(f"ALL PASS ({len(fns)})")


if __name__ == "__main__":
    _run_all()
