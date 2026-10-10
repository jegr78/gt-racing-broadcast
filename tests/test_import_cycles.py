#!/usr/bin/env python3
"""The shared modules under src/scripts must form an acyclic import graph."""
import ast
import os

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src", "scripts")


def _graph():
    """module -> local modules it imports anywhere, function-local imports included."""
    mods = {f[:-3] for f in os.listdir(SCRIPTS) if f.endswith(".py")}
    graph = {}
    for mod in mods:
        with open(os.path.join(SCRIPTS, mod + ".py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                deps.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                deps.add(node.module.split(".")[0])
        graph[mod] = sorted((deps & mods) - {mod})
    return graph


def _cycles(graph):
    state, stack, found = {}, [], []

    def visit(mod):
        state[mod] = "open"
        stack.append(mod)
        for dep in graph[mod]:
            if state.get(dep) == "open":
                found.append(stack[stack.index(dep):] + [dep])
            elif dep not in state:
                visit(dep)
        stack.pop()
        state[mod] = "done"

    for mod in sorted(graph):
        if mod not in state:
            visit(mod)
    return found


def t_cycle_finder_reports_a_two_module_cycle():
    assert _cycles({"a": ["b"], "b": ["a"]}) == [["a", "b", "a"]]
    assert _cycles({"a": ["b"], "b": []}) == []


def t_scripts_have_no_import_cycle():
    cycles = _cycles(_graph())
    assert not cycles, "import cycles under src/scripts: " + "; ".join(" -> ".join(c) for c in cycles)


if __name__ == "__main__":
    for name, fn in sorted(globals().copy().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
