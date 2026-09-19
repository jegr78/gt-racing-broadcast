#!/usr/bin/env python3
"""Stdlib checks over the repo skills (.claude/skills/*/SKILL.md): a recipe must
leave the machine's real state alone. It must never overwrite or delete the shared
cookie jars under runtime/ (on a maintainer machine that also produces broadcasts
those are the real logged-in sessions, #617), and never switch the active-profile
pointer with `profile use` (the next real `event start` would run the demo league).

The rules match line by line on plain text, so they have two known limits:
- REMOVES_JAR also fires on prose such as "never delete runtime/yt-cookies.txt".
  Name the shared jar without a remove/delete verb on the same line.
- DEMO_RELAY only knows the literal `--profile demo relay start|restart` form. A demo
  relay started another way (`event start`, the Control Center, a line continuation
  that puts --cookies on the next line) is not checked.
Run: python3 tests/test_skill_recipes.py"""
import glob, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILLS = os.path.join(ROOT, ".claude", "skills")

JAR = r"runtime/(?:yt|twitch)-cookies\.txt"
# A shell redirect, copy or move onto a shared jar, or a line that tells the reader to remove it.
WRITES_JAR = re.compile(r">>?\s*['\"]?" + JAR + r"|\b(?:cp|mv|tee)\b[^\n]*" + JAR)
REMOVES_JAR = re.compile(r"(?:\brm\b|remove|delete)[^\n]*" + JAR, re.IGNORECASE)
# The CLI always injects the shared jar as --cookies; a demo relay must override it.
DEMO_RELAY = re.compile(r"--profile demo relay (?:start|restart)\b")
# `profile use` writes runtime/active-profile; a recipe passes --profile instead.
PROFILE_USE = re.compile(r"racecast(?:\.py)? profile use\b")


def jar_violations(text):
    """Return (line_no, line) for every line that writes to or removes a shared
    jar, runs `profile use`, or starts/restarts a demo relay without its own stub
    --cookies."""
    bad = []
    for no, line in enumerate(text.splitlines(), 1):
        if WRITES_JAR.search(line) or REMOVES_JAR.search(line) or PROFILE_USE.search(line):
            bad.append((no, line))
        elif DEMO_RELAY.search(line) and ("--cookies" not in line or re.search(JAR, line)):
            bad.append((no, line))
    return bad


def t_detects_stub_written_over_the_shared_jar():
    text = "mkdir -p runtime && printf '# Netscape HTTP Cookie File\\n' > runtime/yt-cookies.txt\n"
    assert jar_violations(text) == [(1, text.rstrip("\n"))]
    assert [n for n, _ in jar_violations("cp /tmp/stub runtime/yt-cookies.txt\n")] == [1]
    assert [n for n, _ in jar_violations("x\nprintf x | tee runtime/twitch-cookies.txt\n")] == [2]


def t_detects_removal_of_the_shared_jar():
    assert [n for n, _ in jar_violations("rm -f runtime/yt-cookies.txt\n")] == [1]
    assert [n for n, _ in jar_violations("x\nremove the stub `runtime/twitch-cookies.txt`\n")] == [2]


def t_detects_demo_relay_without_its_own_jar():
    assert [n for n, _ in jar_violations("python3 src/racecast.py --profile demo relay start\n")] == [1]
    assert [n for n, _ in jar_violations("python3 src/racecast.py --profile demo relay restart\n")] == [1]
    shared = "python3 src/racecast.py --profile demo relay start --cookies \"$PWD/runtime/yt-cookies.txt\"\n"
    assert [n for n, _ in jar_violations(shared)] == [1]


def t_detects_profile_use():
    assert [n for n, _ in jar_violations("python3 src/racecast.py profile use demo\n")] == [1]
    assert jar_violations("python3 src/racecast.py --profile demo ui --no-browser\n") == []


def t_accepts_a_stub_outside_the_shared_jar():
    text = ("printf '# Netscape HTTP Cookie File\\n' > runtime/demo/stub-cookies.txt\n"
            "python3 src/racecast.py --profile demo relay start --cookies \"$PWD/runtime/demo/stub-cookies.txt\"\n"
            "rm -f runtime/demo/stub-cookies.txt\n"
            "The shared jar `runtime/yt-cookies.txt` is the real login; leave it alone.\n")
    assert jar_violations(text) == []


def t_repo_skills_leave_the_machine_state_alone():
    paths = sorted(glob.glob(os.path.join(SKILLS, "*", "SKILL.md")))
    assert paths, SKILLS
    found = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for no, line in jar_violations(fh.read()):
                found.append(f"{os.path.relpath(path, ROOT)}:{no}: {line.strip()}")
    assert found == [], "\n".join(found)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
