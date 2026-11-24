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
    i = html.index("function senderArgs(")
    src = html[i:html.index("\n  }\n", i) + 4]
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
    for bad in ("192.168.1.256", "192.168.1", "1.2.3.4; rm -rf ~", "ps5.local", "01.2.3.4"):
        got = _call(f"senderArgs('https://h.example', 'C1', {json.dumps(bad)})")
        if got is None:
            return
        assert got is False, f"{bad!r} must never be pasted into a shell command: {got}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
