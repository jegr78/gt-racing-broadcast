#!/usr/bin/env python3
"""Prove a comment-cleanup diff changed no logic.

Compares each file's AST against its state at a base ref. Docstrings are dropped
and prose string constants are blanked, so reworded operator text does not read
as a logic change. Anything else that differs fails.

A changed string without whitespace is never prose. Flags, hex digests, dict
keys, env names and URLs are treated as logic, not wording.

    python3 tools/ast-gate.py <base-ref> [path ...]
"""
import ast
import subprocess
import sys

PROSE = "<prose>"


def is_prose(value):
    return " " in value.strip()


class Blank(ast.NodeTransformer):
    def visit_Assert(self, node):
        """An assertion message is prose, so adding or rewording one is not logic."""
        if (isinstance(node.msg, ast.Constant) and isinstance(node.msg.value, str)
                and is_prose(node.msg.value)):
            node.msg = None
        self.generic_visit(node)
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str) and is_prose(node.value):
            return ast.copy_location(ast.Constant(value=PROSE), node)
        return node


def strip_docstrings(tree):
    kept = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            kept.append(body[0].value.value)
            node.body = body[1:] or [ast.Pass()]
    return tree, kept


def analyse(src, name):
    tree, docs = strip_docstrings(ast.parse(src, name))
    # Assert messages are excluded: adding one shifts every later string and would
    # drown the positional diff. They are prose by definition, read in the diff.
    msgs = {id(n.msg) for n in ast.walk(tree) if isinstance(n, ast.Assert) and n.msg}
    prose = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and is_prose(n.value) and id(n) not in msgs]
    return ast.dump(Blank().visit(tree)), prose, docs


def report(label, old, new):
    """Positional diff, so two swapped strings do not cancel out."""
    out = []
    for i in range(max(len(old), len(new))):
        o = old[i] if i < len(old) else None
        n = new[i] if i < len(new) else None
        if o != n:
            out.append((label, o, n))
    return out


def main():
    base, paths = sys.argv[1], sys.argv[2:]
    if not paths:
        paths = subprocess.run(
            ["git", "diff", "--name-only", base, "--", "*.py"],
            capture_output=True, text=True, check=True).stdout.split()
    bad, changes, checked = [], [], 0
    for p in paths:
        old = subprocess.run(["git", "show", f"{base}:{p}"],
                             capture_output=True, text=True)
        if old.returncode:
            print(f"NEW  {p}")
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                new = fh.read()
        except FileNotFoundError:
            print(f"GONE {p}")
            continue
        try:
            shape_o, prose_o, docs_o = analyse(old.stdout, p)
            shape_n, prose_n, docs_n = analyse(new, p)
        except SyntaxError as exc:
            bad.append(f"{p}: SyntaxError: {exc}")
            continue
        checked += 1
        if shape_o != shape_n:
            bad.append(f"{p}: LOGIC CHANGED (structure or a non-prose string)")
        diffs = report("string", prose_o, prose_n)
        # A module docstring feeding argparse or USAGE is CLI output, not a comment.
        if "__doc__" in new:
            diffs += report("docstring (CLI output)", docs_o, docs_n)
        if diffs:
            changes.append((p, diffs))

    for p, diffs in changes:
        print(f"\nREWORDED {p} ({len(diffs)}), review each:")
        for label, o, n in diffs:
            print(f"  - [{label}] {o!r}")
            print(f"  + [{label}] {n!r}")
    for b in bad:
        print(f"FAIL {b}")
    print(f"\n{checked} file(s) checked, {len(bad)} logic failure(s), "
          f"{len(changes)} file(s) with reworded text")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
