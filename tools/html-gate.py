#!/usr/bin/env python3
"""Check that a comment-only edit to an HTML page left everything else intact.

There is no AST to compare for a page, so this compares the document with its
comments removed. If that is byte-identical (modulo whitespace) to the base ref,
the edit touched comments and nothing else. Inline <script> blocks are also
syntax-checked with node.

Comment stripping is context-aware. A single regex over the whole file does not
work: an apostrophe in ordinary HTML text ("every job's output") opens a fake JS
string that then swallows a real /* */ delimiter, and the file reads as changed
when only a comment moved.

    python3 tools/html-gate.py <base-ref> <file> [file ...]
"""
import os
import re
import subprocess
import sys
import tempfile

BLOCK = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1>", re.S | re.I)
HTML_C = re.compile(r"<!--.*?-->", re.S)


# A "/" starts a regex literal only where a value may begin. After a value
# (identifier, literal, closing bracket) it is division. Without this, the class
# in /[&<>"\']/g reads as a string opener and every later comment survives.
_BEFORE_REGEX = set("(,=:[!&|?{};+-*%~^<>") | {"return", "typeof", "case", "in",
                                               "of", "new", "delete", "void",
                                               "instanceof", "do", "else", "yield"}


def _regex_allowed(out):
    for ch in reversed(out):
        if ch.isspace():
            continue
        if ch in _BEFORE_REGEX:
            return True
        if ch.isalnum() or ch in "_$":
            word = ""
            for c in reversed(out):
                if c.isalnum() or c in "_$":
                    word = c + word
                else:
                    break
            return word in _BEFORE_REGEX
        return False
    return True


def strip_code_comments(src):
    """Remove // and /* */ comments from JS or CSS, honouring strings and regexes."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            q, j = c, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == q:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
        elif src.startswith("//", i):
            i = src.find("\n", i)
            if i < 0:
                break
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == "/" and _regex_allowed(out):
            j, cls = i + 1, False
            while j < n:
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "[":
                    cls = True
                elif d == "]":
                    cls = False
                elif d == "/" and not cls:
                    j += 1
                    break
                elif d == "\n":
                    break
                j += 1
            out.append(src[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip(text):
    text = HTML_C.sub("", text)
    return BLOCK.sub(
        lambda m: f"<{m.group(1)}>{strip_code_comments(m.group(2))}</{m.group(1)}>",
        text)


def norm(text):
    return re.sub(r"\s+", " ", strip(text)).strip()


def check_scripts(path, text):
    bad = []
    for i, m in enumerate(BLOCK.finditer(text)):
        if m.group(1).lower() != "script" or not m.group(2).strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(m.group(2))
            tmp = fh.name
        try:
            r = subprocess.run(["node", "--check", tmp], capture_output=True,
                               text=True, errors="replace")
        except FileNotFoundError:
            os.unlink(tmp)
            print("NOTE node not on PATH; the byte-identity check still ran")
            return bad
        os.unlink(tmp)
        if r.returncode:
            line = r.stderr.strip().splitlines()
            bad.append(f"{path}: <script> #{i + 1} does not parse: "
                       f"{line[-3] if len(line) > 2 else r.stderr.strip()}")
    return bad


def main():
    base, paths = sys.argv[1], sys.argv[2:]
    bad = []
    for p in paths:
        old = subprocess.run(["git", "show", f"{base}:{p}"],
                             capture_output=True, text=True)
        with open(p, encoding="utf-8") as fh:
            new = fh.read()
        if old.returncode == 0 and norm(old.stdout) != norm(new):
            bad.append(f"{p}: NON-COMMENT CONTENT CHANGED")
        bad += check_scripts(p, new)
    for b in bad:
        print(f"FAIL {b}")
    print(f"{len(paths)} page(s) checked, {len(bad)} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
