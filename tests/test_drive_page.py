#!/usr/bin/env python3
"""The driver invite page's sender command (src/console/drive.html), run under node.
Run: python3 tests/test_drive_page.py"""
import json, os, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "src", "console", "drive.html")


def _call(expr):
    """Evaluate `expr` beside the page's senderArgs; None where node is missing."""
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    with open(PAGE, encoding="utf-8") as fh:
        html = fh.read()
    i = html.index("function consoleProblem(")
    j = html.index("function senderArgs(")
    src = html[i:html.index("\n  }\n", j) + 4]
    out = subprocess.run([node, "-"], input=f"{src}\nconsole.log(JSON.stringify({expr}));",
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         check=True, timeout=30).stdout
    return json.loads(out)


BASE = "--url https://h.example/console/drive/ingest --code C1"


def t_without_a_console_address_the_command_is_unchanged():
    got = _call("senderArgs('https://h.example', 'C1', '')")
    if got is not None:
        assert got == BASE, f"an empty field must leave the command as registered: {got}"


def t_a_console_address_goes_into_the_command():
    got = _call("senderArgs('https://h.example', 'C1', ' 192.168.1.20 ')")
    if got is not None:
        assert got == BASE + " --console 192.168.1.20", \
            f"the PlayStation IP must reach the command for a-Shell: {got}"


def t_only_a_plain_ipv4_address_reaches_the_shell():
    for bad in ("192.168.1.256", "192.168.1", "1.2.3.4; rm -rf ~", "ps5.local", "01.2.3.4",
                "0.0.0.0", "255.255.255.255", "127.0.0.1", "224.0.0.1"):
        got = _call(f"senderArgs('https://h.example', 'C1', {json.dumps(bad)})")
        if got is None:
            return
        assert got is False, f"{bad!r} must never be pasted into a shell command: {got}"


def t_a_non_lan_address_says_why_it_is_refused():
    got = _call("[consoleProblem('127.0.0.1'), consoleProblem('224.0.0.1'), "
                "consoleProblem('192.168.1'), consoleProblem('192.168.1.20')]")
    if got is not None:
        loop, multicast, short, ok = got
        assert "home network" in loop and loop == multicast, f"a non-LAN address needs its reason: {got}"
        assert "four numbers" in short and ok == "", f"a malformed address keeps its own hint: {got}"


def _view(code, body):
    """statusView(code, body) of the page under node; None where node is missing."""
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    with open(PAGE, encoding="utf-8") as fh:
        html = fh.read()
    i = html.index("  function statusView(")
    src = html[i:html.index("\n  }\n", i) + 4]
    out = subprocess.run([node, "-"], input=f"{src}\nconsole.log(JSON.stringify(statusView({code}, {json.dumps(body)})));",
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         check=True, timeout=30).stdout
    return json.loads(out)


def t_the_status_shows_each_state_of_the_registration():
    got = _view(200, {"state": "quiet", "entry": "Team A #1", "segment": "Stint 2", "name": "Max", "quiet_s": 42})
    if got is None:
        return
    assert got["label"] == "No data" and "for 42 s" in got["text"] and got["who"] == "Team A #1 · Stint 2 · Max", got
    assert not got["stop"], "a quiet sender may come back, so the page keeps asking"
    for state, label in (("pending", "Waiting"), ("approved", "Approved"), ("receiving", "Live")):
        assert _view(200, {"state": state})["label"] == label, state


def t_a_final_state_ends_the_polling_and_forgets_the_code():
    for code, body in ((200, {"state": "rejected"}), (200, {"state": "disconnected"}), (403, {"error": "x"})):
        got = _view(code, body)
        if got is None:
            return
        assert got["stop"] and got["forget"] and got["cls"] == "bad", (code, body, got)


def t_an_answer_the_page_cannot_read_never_shows_as_waiting():
    for code, body in ((429, {"error": "too many"}), (500, None), (404, {"error": "not found"}),
                       (0, None), (200, {"state": "new-thing"})):
        got = _view(code, body)
        if got is None:
            return
        assert got["error"] and got["label"] == "Unknown" and not got["stop"], (code, body, got)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
