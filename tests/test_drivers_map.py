"""Track map dot placement on the driver dashboard (src/obs/drivers.html), run under node."""
import json, math, os, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "src", "obs", "drivers.html")


def _place(spots):
    """placeDots on a circle of radius 40 around (50, 50); None where node is missing."""
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    with open(PAGE, encoding="utf-8") as fh:
        html = fh.read()
    i = html.index("function placeDots(")
    src = html[i:html.index("\n  }\n", i) + 4]
    line = [[50 + 40 * math.cos(math.radians(a)),
             50 + 40 * math.sin(math.radians(a))] for a in range(360)]
    out = subprocess.run([node, "-"], input=f"const CLEAR = 6;\n{src}\n"
                         f"console.log(JSON.stringify(placeDots({json.dumps(line)}, {json.dumps(spots)})));",
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         check=True, timeout=30).stdout
    return json.loads(out)


def _off_line(p):
    return abs(((p[0] - 50) ** 2 + (p[1] - 50) ** 2) ** 0.5 - 40)


def t_a_dot_sits_on_the_track_line():
    got = _place([[93.0, 50.0]])
    if got is not None:
        assert _off_line(got[0]) < 0.05, f"a car 3 units off the line is drawn on it: {got}"


def t_a_dot_behind_slides_back_along_the_line():
    got = _place([[90.0, 50.0], [90.0, 50.0]])
    if got is not None:
        a, b = got
        assert _off_line(b) < 0.05, f"the second dot stays on the line: {b}"
        assert ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= 5.99, f"no overlap: {got}"
        assert b[1] < a[1], f"it moves back against the direction of travel (+y here): {got}"


def t_a_dense_pack_stays_on_the_line():
    got = _place([[90.0, 50.0]] * 12)
    if got is not None:
        off = [p for p in got if _off_line(p) > 0.05]
        assert not off, f"no dot of a bunched field leaves the track line: {off}"



def t_a_sliding_dot_never_passes_the_car_behind():
    got = _place([[90.0, 50.0], [90.0, 50.0], [math.cos(-0.05) * 40 + 50, math.sin(-0.05) * 40 + 50]])
    if got is not None:
        a, b, c = got
        assert b[1] > c[1] - 0.01, f"the second car stays ahead of the third: {got}"

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
