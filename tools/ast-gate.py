#!/usr/bin/env python3
"""Prove a comment-cleanup diff changed no logic.

Compares each file's AST against its state at a base ref. Docstrings are dropped;
string constants are blanked so reworded operator text does not read as a logic
change. Any remaining difference is a real structural change and fails.

Reworded strings are reported so each one can be reviewed and test-covered.

    python3 ast_gate.py <base-ref> [path ...]
"""
import ast
import subprocess
import sys


class Blank(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="<str>"), node)
        return node


def strip_docstrings(tree):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def shape(src, name):
    return ast.dump(Blank().visit(strip_docstrings(ast.parse(src, name))))


def strings(src, name):
    tree = strip_docstrings(ast.parse(src, name))
    return sorted(n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str))


def main():
    base, paths = sys.argv[1], sys.argv[2:]
    if not paths:
        paths = subprocess.run(
            ["git", "diff", "--name-only", base, "--", "*.py"],
            capture_output=True, text=True, check=True).stdout.split()
    bad, reworded, checked = [], [], 0
    for p in paths:
        old = subprocess.run(["git", "show", f"{base}:{p}"],
                             capture_output=True, text=True)
        if old.returncode:
            print(f"NEW  {p}")
            continue
        try:
            new = open(p, encoding="utf-8").read()
        except FileNotFoundError:
            print(f"GONE {p}")
            continue
        try:
            same = shape(old.stdout, p) == shape(new, p)
            so, sn = strings(old.stdout, p), strings(new, p)
        except SyntaxError as exc:
            bad.append(f"{p}: SyntaxError: {exc}")
            continue
        checked += 1
        if not same:
            bad.append(f"{p}: LOGIC CHANGED")
        gone, came = set(so) - set(sn), set(sn) - set(so)
        if gone or came:
            reworded.append((p, sorted(gone), sorted(came)))

    for p, gone, came in reworded:
        print(f"\nSTRINGS {p}  (-{len(gone)} / +{len(came)}) — review each:")
        for g in gone:
            print(f"  - {g!r}")
        for c in came:
            print(f"  + {c!r}")
    for b in bad:
        print(f"FAIL {b}")
    print(f"\n{checked} file(s) checked, {len(bad)} logic failure(s), "
          f"{len(reworded)} file(s) with reworded strings")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
