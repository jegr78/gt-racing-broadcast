#!/usr/bin/env python3
"""Stdlib checks over the repo skills (.claude/skills/*/SKILL.md): a recipe must
never overwrite or delete the shared cookie jars under runtime/. On a maintainer
machine that also produces broadcasts those are the real logged-in sessions (#617).
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


def jar_violations(text):
    """Return (line_no, line) for every line that writes to or removes a shared
    jar, and every demo relay start/restart that does not pass its own --cookies."""
    bad = []
    for no, line in enumerate(text.splitlines(), 1):
        if WRITES_JAR.search(line) or REMOVES_JAR.search(line):
            bad.append((no, line))
        elif DEMO_RELAY.search(line) and "--cookies" not in line:
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


def t_accepts_a_stub_outside_the_shared_jar():
    text = ("printf '# Netscape HTTP Cookie File\\n' > runtime/demo/stub-cookies.txt\n"
            "python3 src/racecast.py --profile demo relay start --cookies \"$PWD/runtime/demo/stub-cookies.txt\"\n"
            "rm -f runtime/demo/stub-cookies.txt\n"
            "The shared jar `runtime/yt-cookies.txt` is the real login; leave it alone.\n")
    assert jar_violations(text) == []


def t_repo_skills_leave_the_shared_jars_alone():
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
