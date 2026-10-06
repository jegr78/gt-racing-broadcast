# Visual acceptance run (#772) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python3 tools/e2e.py --visual --report DIR` renders the Control Center views, the Director Panel, the Commentator Cockpit and the Race Control desk in fixed viewports, applies six deterministic layout rules, writes an HTML report and fails on any finding; a CI job runs it on every PR.

**Architecture:** The synthetic e2e run (`tools/e2e.py`) already stands up a relay and the Control Center. It gains a Crew CSV (so a Race Control token exists) and `tools/obs-sim.py` (so the program monitor shows a picture). A browser-side probe (`tools/visual-probe.js`) turns each rendered page into a JSON list of element facts; pure Python rules in `tools/e2e_visual.py` turn the facts into findings, an allowlist suppresses deliberate exceptions, and a pure renderer writes the report. Only the driver in `e2e.py` touches Playwright.

**Tech Stack:** Python 3.11+ stdlib, Playwright for Python 1.63.0 (optional locally, installed in the CI `visual` job), Chromium, GitHub Actions.

## Global Constraints

- Branch `feat/772-visual-acceptance` off current `main`. One PR for the whole issue, squash-merge after green CI.
- Every task commit ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- All code, comments, docs and commit messages in English. argparse `help=` strings ASCII only (`->`, never a Unicode arrow).
- Playwright is never imported at module top level: lazy import with `# noqa: PLC0415  optional, lazy`, exactly like `tools/e2e.py` does today.
- Rule logic stays browser-free and runs in `tests/` on Linux, macOS and Windows: no paths, no real IPs, no OS-specific values in tests.
- Do not change `--shots`, `--playwright`, `RENDERED_CHECKS` or the stdlib `e2e` CI job. The new run is a sibling.
- Comments: one reason, one sentence. Docstring says what the function does and what the caller must know.
- After every Python change: `python3 tools/lint.py`. After the relay change: `python3 tests/test_pov.py`. Before the PR: `python3 tools/run-tests.py` and `python3 tools/build.py`.
- Read `src/relay/CLAUDE.md` before Task 1 (relay change) and `tools/CLAUDE.md` before Task 6.
- Do not edit `src/director/`, `src/cockpit/`, `src/racecontrol/`, `src/console/` or `src/ui/` in this PR (see Task 7). Such an edit triggers the `ui_visual_verify_gate` Stop hook and the same-change wiki-screenshot rule.

## File structure

| File | Responsibility |
|---|---|
| `src/relay/racecast-feeds.py` (modify) | `crew_csv_url()` helper + `--crew-csv-url` flag, so a custom schedule URL can still have a Crew roster |
| `tools/e2e_checks.py` (modify) | `build_crew_csv()`, `Ctx.rc_token`, `check_race_control_page`, `check_program_monitor` |
| `tools/e2e.py` (modify) | path-aware CSV stub, crew rows, obs-sim, `--visual`/`--report`, the Playwright driver |
| `tools/e2e_visual.py` (create) | pure: element-fact schema, colour maths, six rules, allowlist, surfaces, report renderer |
| `tools/visual-probe.js` (create) | browser side: one function returning the element facts as JSON |
| `tools/visual-allowlist.json` (create) | deliberate exceptions, each with a reason |
| `tests/test_e2e_visual.py` (create) | unit tests for `e2e_visual.py`, including a #397 reproduction |
| `tests/test_e2e.py`, `tests/test_roles.py` (modify) | tests for the new e2e_checks pieces and the crew URL helper |
| `.github/workflows/ci.yml` (modify) | new `visual` job |
| `tools/CLAUDE.md`, `.claude/skills/ui-visual-verification/SKILL.md` (modify) | document the run |

## Element-fact schema (the contract between Tasks 3 to 6)

`visual-probe.js` returns one object per page. Every rule consumes only these keys.

```text
facts = {
  "viewport":     {"w": int, "h": int},          # documentElement.clientWidth / innerHeight
  "scroll_width": int,                           # documentElement.scrollWidth
  "ua":           {"input": str, "select": str, "textarea": str, "button": str},
                                                 # UA-default computed background-color per control
  "elements": [ {                                # visible elements only, document order
      "i": int,                                  # index into elements
      "sel": str,                                # short CSS path, "#id" when the element has one
      "tag": str,                                # lower-case tag name
      "interactive": bool,
      "text": str,                               # own text nodes, trimmed, max 60 chars
      "has_text": bool,                          # textContent non-empty (descendants included)
      "rect": [x, y, w, h],                      # document coordinates, px
      "client_w": int, "scroll_w": int, "client_h": int, "scroll_h": int,
      "overflow_x": str, "overflow_y": str, "text_overflow": str, "line_clamp": bool,
      "color": [r, g, b, a] | None,              # None when not rgb()/rgba()
      "bg_chain": [[r, g, b, a], ...],           # non-transparent backgrounds, element first, up to the first opaque one
      "bg_image": bool,                          # a background-image or unparsable colour in that chain
      "opacity": float,                          # product of the element's and its ancestors' opacity
      "font_size": float, "font_weight": int,
      "disabled": bool,
      "ua_key": "input" | "select" | "textarea" | "button" | None,
      "bg_raw": str,                             # computed background-color string
      "parent_bg": [[r, g, b, a], ...],          # bg_chain of the parent element
      "anc": [int, ...]                          # indices of interactive ancestors
  } ]
}
```

---

### Task 1: Crew roster for a custom schedule URL, Race Control reachable in the synthetic run

The relay builds its Crew roster only when no `--sheet-csv-url` is given (`src/relay/racecast-feeds.py:12347-12357`). The synthetic e2e run passes one, so no token ever gets the `race_control` capability and `/console/race-control` answers 403. This task adds a seam and proves Race Control works in the stdlib harness.

**Files:**
- Modify: `src/relay/racecast-feeds.py` (new helper next to `class CrewSource`, flag next to `--crew-tab` at ~12178, wiring at ~12347)
- Modify: `tools/e2e_checks.py` (after `build_schedule_csv`, `Ctx`, new check, `SYNTHETIC_CHECKS`)
- Modify: `tools/e2e.py` (`_csv_server`, `SCHEDULE_ROWS` block, `run_synthetic` steps 1 to 3 and 7)
- Test: `tests/test_roles.py`, `tests/test_e2e.py`

**Interfaces:**
- Produces: `crew_csv_url(sheet_id, crew_tab, sheet_csv_url=None, override=None) -> str | None` in the relay module.
- Produces: relay flag `--crew-csv-url URL`.
- Produces: `e2e_checks.CREW_HEADER`, `e2e_checks.build_crew_csv(rows) -> str`.
- Produces: `e2e_checks.Ctx` gains a trailing optional field `rc_token` (default `None`).
- Produces: `e2e_checks.check_race_control_page(ctx) -> CheckResult`.
- Produces: `e2e._csv_server(files: dict[str, str]) -> (server, base_url)`.

- [ ] **Step 1: Create the branch**

```bash
git fetch origin && git switch -c feat/772-visual-acceptance origin/main
```

- [ ] **Step 2: Write the failing unit test for the URL helper** (append to `tests/test_roles.py` before the `__main__` block)

```python
def t_crew_csv_url_derivation():
    assert m.crew_csv_url("SHEET", "Crew") == (
        "https://docs.google.com/spreadsheets/d/SHEET/gviz/tq?tqx=out:csv&sheet=Crew")
    assert m.crew_csv_url("SHEET", "Crew", sheet_csv_url="http://h/s.csv") is None, \
        "a custom schedule URL leaves no tab to derive the roster from"
    assert m.crew_csv_url("SHEET", "Crew", sheet_csv_url="http://h/s.csv",
                          override="http://h/c.csv") == "http://h/c.csv"
```

- [ ] **Step 3: Run it, expect FAIL**

Run: `python3 tests/test_roles.py`
Expected: `AttributeError: module 'irofeeds' has no attribute 'crew_csv_url'`

- [ ] **Step 4: Add the helper** (directly above `class CrewSource:`)

```python
def crew_csv_url(sheet_id, crew_tab, sheet_csv_url=None, override=None):
    """The Crew roster CSV URL, or None when a custom schedule URL leaves no tab to derive it from."""
    if override:
        return override
    if sheet_csv_url:
        return None
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={quote(crew_tab)}")
```

Check that `quote` is imported at module level (`grep -n "^from urllib.parse import\|^import urllib" src/relay/racecast-feeds.py`); the existing wiring already calls it inside `main()`.

- [ ] **Step 5: Add the flag and use the helper in `main()`**

Next to the `--crew-tab` argument:

```python
    ap.add_argument("--crew-csv-url", default=None,
                    help="Full Crew roster CSV URL (overrides the derived Crew tab, "
                         "also with --sheet-csv-url)")
```

Replace the block at ~12351-12357:

```python
    crew_source = None
    crew_url = crew_csv_url(args.sheet_id, args.crew_tab, args.sheet_csv_url, args.crew_csv_url)
    if crew_url:
        crew_cache = os.path.join(runtime, "crew.cache.txt")
        crew_source = CrewSource(crew_url, crew_cache)
        crew_source.refresh()   # non-fatal: empty/unreachable = no director/producer rows
```

Keep the comment block above it, but change "so a custom --sheet-csv-url disables it" to "so a custom --sheet-csv-url disables it unless --crew-csv-url names one".

- [ ] **Step 6: Run the relay tests, expect PASS**

Run: `python3 tests/test_roles.py && python3 tests/test_pov.py`
Expected: `ALL PASS` and the POV suite passing.

- [ ] **Step 7: Write the failing e2e_checks tests** (append to `tests/test_e2e.py` before `__main__`)

```python
def t_build_crew_csv_header_and_rows():
    text = e.build_crew_csv([("Rita", "", "", "", "x")])
    lines = text.strip().splitlines()
    assert lines[0] == "Name,Director,Producer,Commentator,Race Control", lines[0]
    assert lines[1] == "Rita,,,,x", lines[1]


def t_race_control_check_skips_without_token():
    ctx = e.Ctx(relay_url="http://127.0.0.1:9", disabled_relay_url=None, ui_url=None,
                token="t", streamer_key="alice", expect={})
    r = e.check_race_control_page(ctx)
    assert r.status == "skip", r
```

- [ ] **Step 8: Run, expect FAIL**

Run: `python3 tests/test_e2e.py`
Expected: `AttributeError: module 'e2e_checks' has no attribute 'build_crew_csv'`

- [ ] **Step 9: Implement in `tools/e2e_checks.py`**

Below `build_schedule_csv`:

```python
CREW_HEADER = ("Name", "Director", "Producer", "Commentator", "Race Control")


def build_crew_csv(rows):
    """A header-mode Crew-tab CSV the relay's CrewSource parses; *rows* follow CREW_HEADER."""
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(CREW_HEADER)
    for row in rows:
        w.writerow(row)
    return buf.getvalue()
```

Replace the `Ctx` definition:

```python
Ctx = collections.namedtuple(
    "Ctx",
    "relay_url disabled_relay_url ui_url token streamer_key expect own_stint"
    " fanout_feed_port fanout_relay_url rc_token")
Ctx.__new__.__defaults__ = (None, None, None, None)  # own_stint, fanout_feed_port,
                                                      # fanout_relay_url, rc_token are optional
```

New check (after `check_cc_api_cockpit`):

```python
def check_race_control_page(ctx):
    """A crew member with the Race Control flag gets the desk, a plain commentator gets 403."""
    name = "race_control_page"
    if not ctx.rc_token:
        return CheckResult(name, "skip", "no race-control token")
    st, _, _ = http_request(f"{ctx.relay_url}/console/race-control?t={ctx.rc_token}")
    if st != 200:
        return CheckResult(name, "fail", f"race-control token: HTTP {st}")
    st, _, _ = http_request(f"{ctx.relay_url}/console/race-control?t={ctx.token}")
    if st != 403:
        return CheckResult(name, "fail", f"commentator token: HTTP {st}, want 403")
    return CheckResult(name, "pass", "")
```

Append `check_race_control_page` to `SYNTHETIC_CHECKS` (not to `REAL_LEAGUE_CHECKS`).

- [ ] **Step 10: Run, expect PASS**

Run: `python3 tests/test_e2e.py`
Expected: `PASS test_e2e`

- [ ] **Step 11: Make the CSV stub path-aware and serve a crew roster** (`tools/e2e.py`)

Replace `_csv_server`:

```python
def _csv_server(files):
    """Serve each {path: csv_text} entry over loopback HTTP; returns (server, base_url)."""
    bodies = {p: t.encode() for p, t in files.items()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = bodies.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404); self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv"); self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", E.free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
```

Below `SCHEDULE_ROWS`:

```python
# Rita is crew only, so her token carries race_control and nothing else.
CREW_ROWS = [("Rita", "", "", "", "x")]
```

In `run_synthetic`, step 1, after `token = ...`:

```python
        rc_token = console_auth.mint_token(secret, console_auth.streamer_key("Rita"), version=1)
```

Step 2 becomes:

```python
        csv_srv, csv_base = _csv_server({
            "/schedule.csv": E.build_schedule_csv(SCHEDULE_ROWS),
            "/crew.csv": E.build_crew_csv(CREW_ROWS)})
        servers.append(csv_srv)
        csv_url, crew_url = csv_base + "/schedule.csv", csv_base + "/crew.csv"
```

Step 3: add `"--crew-csv-url", crew_url,` right after `"--sheet-csv-url", csv_url,` in the first relay's argv only. Step 7: pass `rc_token=rc_token` to `E.Ctx(...)`.

`grep -n "_csv_server(" tools/*.py` must show only the one call site you just changed.

- [ ] **Step 12: Run the harness, expect the new check to pass**

Run: `python3 tools/e2e.py`
Expected: `[PASS] race_control_page` and no new failures. A 403 for the Rita token means the relay did not load the roster: check `relay.log` in the kept temp dir (`--keep`) for the crew fetch.

- [ ] **Step 13: Lint and commit**

```bash
python3 tools/lint.py
git add src/relay/racecast-feeds.py tools/e2e_checks.py tools/e2e.py tests/test_roles.py tests/test_e2e.py \
        docs/superpowers/plans/2026-10-06-visual-acceptance-run.md
git commit -m "feat(e2e): serve a Crew roster in the synthetic run so Race Control is reachable

The relay gains --crew-csv-url, which keeps the Crew roster alive next to a
custom --sheet-csv-url. The harness mints a Race Control token and checks the
desk answers 200 for it and 403 for a plain commentator.

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: obs-sim in the synthetic run

Without an OBS the Director Panel, cockpit and desk render their OBS-offline state. The visual run should judge the populated state.

**Files:**
- Modify: `tools/e2e.py` (`run_synthetic` before step 3, new `_wait_port`)
- Modify: `tools/e2e_checks.py` (new check, `SYNTHETIC_CHECKS`)
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `tools/obs-sim.py --image PATH --port N` (unchanged).
- Produces: `e2e_checks.check_program_monitor(ctx) -> CheckResult`.

- [ ] **Step 1: Write the failing test** (in `tests/test_e2e.py`)

```python
def t_program_monitor_fails_on_503():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(503); self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", e.free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx = e.Ctx(relay_url=f"http://127.0.0.1:{srv.server_address[1]}",
                    disabled_relay_url=None, ui_url=None, token="t",
                    streamer_key="alice", expect={})
        r = e.check_program_monitor(ctx)
        assert r.status == "fail", r
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 tests/test_e2e.py`
Expected: `AttributeError: ... no attribute 'check_program_monitor'`

- [ ] **Step 3: Implement the check** (after `check_race_control_page`)

```python
def check_program_monitor(ctx):
    """The cockpit program monitor serves the still obs-sim hands the relay."""
    name = "program_monitor"
    st, body, hdrs = http_request(f"{ctx.relay_url}/cockpit/program?t={ctx.token}")
    if st != 200:
        return CheckResult(name, "fail", f"HTTP {st}")
    ctype = hdrs.get("Content-Type") or ""
    if not ctype.startswith("image/") or not body:
        return CheckResult(name, "fail", f"not an image: {ctype!r}, {len(body)} bytes")
    return CheckResult(name, "pass", "")
```

Append it to `SYNTHETIC_CHECKS`.

- [ ] **Step 4: Run, expect PASS**

Run: `python3 tests/test_e2e.py`
Expected: `PASS test_e2e`

- [ ] **Step 5: Start obs-sim in `run_synthetic`**

Add next to `_wait_ready`:

```python
def _wait_port(port, timeout):
    """Block until 127.0.0.1:*port* accepts a TCP connection or *timeout* seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.5):
            return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} not ready after {timeout}s")
```

Add the imports `socket` and `time` if `tools/e2e.py` lacks them.

In `run_synthetic`, after `stub_bin = _stub_tools_bin(tmp)`:

```python
        # A stand-in OBS, so the program monitors render a picture instead of their offline state.
        obs_port = E.free_port()
        procs.append(_spawn([sys.executable, os.path.join(ROOT, "tools", "obs-sim.py"),
                             "--image", os.path.join(relay_runtime, "graphics", "Standings.png"),
                             "--port", str(obs_port)],
                            dict(os.environ), os.path.join(tmp, "obs-sim.log")))
        _wait_port(obs_port, args.timeout)
```

In step 3's `env.update(...)` add `RACECAST_OBS_WS_HOST="127.0.0.1", RACECAST_OBS_WS_PORT=str(obs_port), RACECAST_OBS_WS_PASSWORD=""`. The Control Center and the fan-out relay copy `env`, so they reach obs-sim too; the secret-less relay copies `os.environ` and stays OBS-less, which is fine.

- [ ] **Step 6: Run the harness**

Run: `python3 tools/e2e.py`
Expected: `[PASS] program_monitor`, and every check that passed before Task 2 still passes. If an earlier check now fails, the relay reacted to a reachable OBS: read the failing check and the relay log before changing anything.

- [ ] **Step 7: Lint and commit**

```bash
python3 tools/lint.py
git add tools/e2e.py tools/e2e_checks.py tests/test_e2e.py
git commit -m "feat(e2e): run obs-sim in the synthetic harness

The relays and the Control Center talk to tools/obs-sim.py, so the program
monitors show a still. A new check proves the cockpit program endpoint serves it.

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Rule core: colour maths, overflow, unstyled controls, overlap, console

**Files:**
- Create: `tools/e2e_visual.py`
- Create: `tests/test_e2e_visual.py`

**Interfaces:**
- Consumes: the element-fact schema above.
- Produces: `Finding = namedtuple("Finding", "rule selector detail")`, `effective_bg(chain) -> (r, g, b)`, `blend(fg, bg) -> (r, g, b)`, `luminance(rgb) -> float`, `contrast_ratio(a, b) -> float`, `rule_overflow(facts)`, `rule_unstyled_controls(facts)`, `rule_overlap(facts)`, `rule_console(errors)`; each rule returns `list[Finding]`. `evaluate(facts, errors) -> list[Finding]` (Task 4 extends it).

- [ ] **Step 1: Write the failing tests** (`tests/test_e2e_visual.py`)

```python
#!/usr/bin/env python3
"""Unit tests for the browser-free rules of the visual acceptance run (tools/e2e_visual.py)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import e2e_visual as v

DARK = [19, 23, 28, 1]          # director-panel --panel
WHITE_UA = "rgb(255, 255, 255)"


def _el(**kw):
    rec = {"i": 0, "sel": "#x", "tag": "div", "interactive": False, "text": "",
           "has_text": False, "rect": [0, 0, 100, 20], "client_w": 100, "scroll_w": 100,
           "client_h": 20, "scroll_h": 20, "overflow_x": "visible", "overflow_y": "visible",
           "text_overflow": "clip", "line_clamp": False, "color": [230, 230, 230, 1],
           "bg_chain": [DARK], "bg_image": False, "opacity": 1.0, "font_size": 14.0,
           "font_weight": 400, "disabled": False, "ua_key": None, "bg_raw": "rgba(0, 0, 0, 0)",
           "parent_bg": [DARK], "anc": []}
    rec.update(kw)
    return rec


def _facts(elements, w=1280, scroll_width=1280):
    return {"viewport": {"w": w, "h": 800}, "scroll_width": scroll_width,
            "ua": {"input": WHITE_UA, "select": WHITE_UA, "textarea": WHITE_UA,
                   "button": "rgb(239, 239, 239)"},
            "elements": elements}


def t_contrast_ratio_extremes():
    assert round(v.contrast_ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert v.contrast_ratio((10, 10, 10), (10, 10, 10)) == 1.0


def t_effective_bg_composites_over_white():
    assert v.effective_bg([]) == (255, 255, 255), "no layer means the white canvas"
    assert v.effective_bg([[0, 0, 0, 0.5]]) == (127.5, 127.5, 127.5)
    assert v.effective_bg([[255, 0, 0, 1], [0, 0, 255, 1]]) == (255, 0, 0), \
        "the element's own opaque layer wins"


def t_overflow_flags_wide_page():
    assert v.rule_overflow(_facts([], w=390, scroll_width=390)) == []
    hits = v.rule_overflow(_facts([], w=390, scroll_width=512))
    assert [f.rule for f in hits] == ["overflow"], hits
    assert "512" in hits[0].detail


def t_unstyled_input_on_dark_panel_is_flagged():
    # The #397 case: a duration input left at the UA white on the dark Director Panel.
    el = _el(sel="#txDur", tag="input", interactive=True, ua_key="input",
             bg_raw=WHITE_UA, bg_chain=[[255, 255, 255, 1]])
    hits = v.rule_unstyled_controls(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("unstyled-control", "#txDur")], hits


def t_styled_input_passes():
    el = _el(tag="input", interactive=True, ua_key="input",
             bg_raw="rgb(23, 28, 34)", bg_chain=[[23, 28, 34, 1]])
    assert v.rule_unstyled_controls(_facts([el])) == []


def t_ua_input_on_light_surface_passes():
    el = _el(tag="input", interactive=True, ua_key="input", bg_raw=WHITE_UA,
             parent_bg=[[245, 245, 245, 1]])
    assert v.rule_unstyled_controls(_facts([el])) == [], "white on light is a design choice"


def t_overlap_flags_siblings_not_nesting():
    a = _el(i=0, sel="#a", interactive=True, rect=[0, 0, 50, 20])
    b = _el(i=1, sel="#b", interactive=True, rect=[40, 0, 50, 20])
    c = _el(i=2, sel="#c", interactive=True, rect=[5, 5, 10, 10], anc=[0])
    d = _el(i=3, sel="#d", interactive=True, rect=[51, 30, 10, 10])
    hits = v.rule_overlap(_facts([a, b, c, d]))
    pairs = sorted((f.selector, f.detail) for f in hits)
    assert pairs == [("#a", "overlaps #b")], pairs


def t_overlap_ignores_hairline_touch():
    a = _el(i=0, sel="#a", interactive=True, rect=[0, 0, 50, 20])
    b = _el(i=1, sel="#b", interactive=True, rect=[49, 0, 50, 20])
    assert v.rule_overlap(_facts([a, b])) == [], "a 1 px shared edge is not an overlap"


def t_console_errors_become_findings():
    hits = v.rule_console(["pageerror: TypeError: x is undefined"])
    assert [(f.rule, f.selector) for f in hits] == [("console", "console")], hits


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_e2e_visual")
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 tests/test_e2e_visual.py`
Expected: `ModuleNotFoundError: No module named 'e2e_visual'`

- [ ] **Step 3: Implement `tools/e2e_visual.py`**

```python
#!/usr/bin/env python3
"""Browser-free core of the visual acceptance run (tools/e2e.py --visual).

tools/visual-probe.js turns a rendered page into element facts (schema in
docs/superpowers/plans/2026-10-06-visual-acceptance-run.md); the rules here
turn facts into findings. Stdlib only, unit-tested by tests/test_e2e_visual.py."""
import collections

Finding = collections.namedtuple("Finding", "rule selector detail")

DARK_SURFACE_LUM = 0.2
OVERLAP_MIN_PX = 2


def blend(fg, bg):
    """*fg* [r, g, b, a] painted over the opaque colour *bg* (r, g, b)."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))


def effective_bg(chain):
    """Composite a background chain (element first, root last) over the white canvas."""
    rgb = (255, 255, 255)
    for layer in reversed(chain):
        rgb = blend(layer, rgb)
    return rgb


def luminance(rgb):
    """WCAG 2 relative luminance of an sRGB colour."""
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    """WCAG 2 contrast ratio of two opaque colours."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def rule_overflow(facts):
    w, sw = facts["viewport"]["w"], facts["scroll_width"]
    if sw > w + 1:
        return [Finding("overflow", "html", f"page is {sw} px wide in a {w} px viewport")]
    return []


def rule_unstyled_controls(facts):
    out = []
    for el in facts["elements"]:
        ua = facts["ua"].get(el["ua_key"]) if el["ua_key"] else None
        if ua is None or el["bg_raw"] != ua:
            continue
        if luminance(effective_bg(el["parent_bg"])) < DARK_SURFACE_LUM:
            out.append(Finding("unstyled-control", el["sel"],
                               f"{el['tag']} keeps the browser default background {ua} on a dark surface"))
    return out


def _intersects(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w >= OVERLAP_MIN_PX and h >= OVERLAP_MIN_PX


def rule_overlap(facts):
    items = [el for el in facts["elements"] if el["interactive"]]
    out = []
    for n, a in enumerate(items):
        for b in items[n + 1:]:
            if a["i"] in b["anc"] or b["i"] in a["anc"]:
                continue
            if _intersects(a["rect"], b["rect"]):
                out.append(Finding("overlap", a["sel"], f"overlaps {b['sel']}"))
    return out


def rule_console(errors):
    return [Finding("console", "console", msg[:300]) for msg in errors]


FACT_RULES = [rule_overflow, rule_unstyled_controls, rule_overlap]


def evaluate(facts, errors):
    """All findings for one rendered page, duplicates removed, order kept."""
    found = [f for rule in FACT_RULES for f in rule(facts)] + rule_console(errors)
    return list(dict.fromkeys(found))
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 tests/test_e2e_visual.py`
Expected: `PASS test_e2e_visual`

- [ ] **Step 5: Lint and commit**

```bash
python3 tools/lint.py
git add tools/e2e_visual.py tests/test_e2e_visual.py
git commit -m "feat(e2e): add the browser-free rule core of the visual run

Overflow, unstyled controls on dark surfaces (#397), overlapping controls and
console errors, evaluated on element facts.

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Clipped text, contrast, allowlist

Both rules depend on fonts, which differ between the CI runner and a producer machine. Tolerances absorb sub-pixel noise; the allowlist takes the rest, each entry with a reason.

**Files:**
- Modify: `tools/e2e_visual.py`
- Create: `tools/visual-allowlist.json`
- Test: `tests/test_e2e_visual.py`

**Interfaces:**
- Produces: `rule_clipped_text(facts)`, `rule_contrast(facts)`, both appended to `FACT_RULES`.
- Produces: `load_allowlist(path) -> list[dict]` (raises `ValueError` on a malformed entry).
- Produces: `apply_allowlist(findings, entries, surface, viewport) -> (kept: list[Finding], used: set[int])`; `used` holds indices into `entries`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_e2e_visual.py`)

```python
def t_clipped_text_without_ellipsis_is_flagged():
    el = _el(sel="#name", has_text=True, overflow_x="hidden", client_w=80, scroll_w=140)
    hits = v.rule_clipped_text(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("clipped-text", "#name")], hits


def t_ellipsis_and_scrollers_pass():
    ell = _el(has_text=True, overflow_x="hidden", client_w=80, scroll_w=140, text_overflow="ellipsis")
    scroller = _el(has_text=True, overflow_x="auto", client_w=80, scroll_w=140)
    subpixel = _el(has_text=True, overflow_x="hidden", client_w=80, scroll_w=81)
    clamp = _el(has_text=True, overflow_y="hidden", client_h=40, scroll_h=90, line_clamp=True)
    assert v.rule_clipped_text(_facts([ell, scroller, subpixel, clamp])) == []


def t_vertical_clip_is_flagged():
    el = _el(sel="#note", has_text=True, overflow_y="hidden", client_h=40, scroll_h=90)
    assert [f.rule for f in v.rule_clipped_text(_facts([el]))] == ["clipped-text"]


def t_low_contrast_text_is_flagged():
    el = _el(sel="#dim", text="ON AIR", color=[60, 66, 74, 1], bg_chain=[DARK])
    hits = v.rule_contrast(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("contrast", "#dim")], hits
    assert ":1" in hits[0].detail


def t_large_text_uses_the_3_to_1_threshold():
    grey = [118, 118, 118, 1]          # ~4.0:1 on DARK
    small = _el(text="x", color=grey, font_size=14.0)
    large = _el(text="x", color=grey, font_size=24.0)
    bold = _el(text="x", color=grey, font_size=19.0, font_weight=700)
    assert len(v.rule_contrast(_facts([small]))) == 1
    assert v.rule_contrast(_facts([large, bold])) == []


def t_contrast_skips_unknowable_cases():
    cases = [_el(text="x", color=[60, 66, 74, 1], bg_image=True),
             _el(text="x", color=[60, 66, 74, 1], disabled=True),
             _el(text="x", color=[60, 66, 74, 1], opacity=0.5),
             _el(text="x", color=None),
             _el(text="", color=[60, 66, 74, 1])]
    assert v.rule_contrast(_facts(cases)) == []


def t_allowlist_matches_surface_viewport_rule_selector():
    entries = [{"surface": "cockpit", "viewport": "phone", "rule": "contrast",
                "selector": "#dim", "reason": "placeholder dash"},
               {"surface": "cockpit", "rule": "console", "selector": "console",
                "detail": "favicon", "reason": "synthetic profile has no favicon"},
               {"surface": "cockpit", "rule": "overlap", "selector": "#never", "reason": "stale"}]
    found = [v.Finding("contrast", "#dim", "2.1:1 < 4.5:1"),
             v.Finding("console", "console", "console.error: GET /favicon.ico 404"),
             v.Finding("contrast", "#other", "2.1:1 < 4.5:1")]
    kept, used = v.apply_allowlist(found, entries, "cockpit", "phone")
    assert kept == [found[2]], kept
    assert used == {0, 1}, used
    kept, _ = v.apply_allowlist(found, entries, "cockpit", "desktop")
    assert found[0] in kept, "a viewport-bound entry must not match another viewport"


def t_allowlist_selector_is_a_full_regex():
    entries = [{"surface": "s", "rule": "contrast", "selector": r"#row-\d+", "reason": "r"}]
    kept, _ = v.apply_allowlist([v.Finding("contrast", "#row-12", "")], entries, "s", "desktop")
    assert kept == []
    kept, _ = v.apply_allowlist([v.Finding("contrast", "#row-12 > b", "")], entries, "s", "desktop")
    assert len(kept) == 1, "the selector must match in full"


def t_load_allowlist_requires_a_reason():
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"surface": "s", "rule": "contrast", "selector": "#a", "reason": ""}], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("an entry without a reason must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)


def t_shipped_allowlist_loads():
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "visual-allowlist.json")
    assert isinstance(v.load_allowlist(path), list)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 tests/test_e2e_visual.py`
Expected: `AttributeError: module 'e2e_visual' has no attribute 'rule_clipped_text'`

- [ ] **Step 3: Implement** (add to `tools/e2e_visual.py`; add `import json` and `import re` at the top)

```python
CLIP_TOLERANCE_PX = 2
CLIPPING = ("hidden", "clip")


def rule_clipped_text(facts):
    out = []
    for el in facts["elements"]:
        if not el["has_text"]:
            continue
        if (el["overflow_x"] in CLIPPING and el["text_overflow"] != "ellipsis"
                and el["scroll_w"] > el["client_w"] + CLIP_TOLERANCE_PX):
            out.append(Finding("clipped-text", el["sel"],
                               f"content {el['scroll_w']} px wide in a {el['client_w']} px box"))
        elif (el["overflow_y"] in CLIPPING and not el["line_clamp"]
                and el["scroll_h"] > el["client_h"] + CLIP_TOLERANCE_PX):
            out.append(Finding("clipped-text", el["sel"],
                               f"content {el['scroll_h']} px high in a {el['client_h']} px box"))
    return out


def _is_large(el):
    return el["font_size"] >= 24 or (el["font_size"] >= 18.66 and el["font_weight"] >= 700)


def rule_contrast(facts):
    out = []
    for el in facts["elements"]:
        if (not el["text"] or el["color"] is None or el["bg_image"] or el["disabled"]
                or el["opacity"] < 1):
            continue
        bg = effective_bg(el["bg_chain"])
        ratio = contrast_ratio(blend(el["color"], bg), bg)
        need = 3.0 if _is_large(el) else 4.5
        if ratio < need:
            out.append(Finding("contrast", el["sel"], f"{ratio:.2f}:1 < {need}:1"))
    return out
```

Change `FACT_RULES` to `[rule_overflow, rule_clipped_text, rule_unstyled_controls, rule_overlap, rule_contrast]`.

Allowlist:

```python
ALLOW_REQUIRED = ("surface", "rule", "selector", "reason")


def load_allowlist(path):
    """Read the allowlist JSON; every entry needs surface, rule, selector and a non-empty reason."""
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        raise ValueError(f"{path}: top level must be a list")
    for n, entry in enumerate(entries):
        missing = [k for k in ALLOW_REQUIRED if not str(entry.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path}: entry {n} lacks {', '.join(missing)}")
    return entries


def _allows(entry, finding, surface, viewport):
    if entry["surface"] != surface or entry["rule"] != finding.rule:
        return False
    if entry.get("viewport") and entry["viewport"] != viewport:
        return False
    if not re.fullmatch(entry["selector"], finding.selector):
        return False
    return not entry.get("detail") or re.search(entry["detail"], finding.detail) is not None


def apply_allowlist(findings, entries, surface, viewport):
    """Drop allowlisted findings. Returns (kept findings, indices of the entries that matched)."""
    kept, used = [], set()
    for f in findings:
        hits = [n for n, e in enumerate(entries) if _allows(e, f, surface, viewport)]
        used.update(hits)
        if not hits:
            kept.append(f)
    return kept, used
```

Create `tools/visual-allowlist.json` with the content `[]` plus a trailing newline.

- [ ] **Step 4: Run, expect PASS**

Run: `python3 tests/test_e2e_visual.py`
Expected: `PASS test_e2e_visual`

- [ ] **Step 5: Lint and commit**

```bash
python3 tools/lint.py
git add tools/e2e_visual.py tools/visual-allowlist.json tests/test_e2e_visual.py
git commit -m "feat(e2e): add clipped-text and contrast rules plus an allowlist

Each allowlist entry names surface, rule, a full-match selector regex and a
reason, optionally a viewport and a detail regex.

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Surfaces and the report

**Files:**
- Modify: `tools/e2e_visual.py`
- Test: `tests/test_e2e_visual.py`

**Interfaces:**
- Produces: `VIEWPORTS = {"desktop": (1280, 800), "phone": (390, 844)}`, `SETTLE_MS = 800`.
- Produces: `Surface = namedtuple("Surface", "name base path viewports prep ready timeout_ms")`; `base` is `"ui"` or `"relay"`; `path` may hold `{token}` and `{rc_token}`; `prep` is JS run after load or `None`; `ready` is a JS expression that turns true once data rendered.
- Produces: `CC_VIEWS`, `SURFACES`, `surface_url(surface, urls) -> str` where `urls = {"ui", "relay", "token", "rc_token"}`.
- Produces: `SurfaceResult = namedtuple("SurfaceResult", "surface viewport shot findings suppressed error")`.
- Produces: `render_report(results, unused_entries) -> str`, `summarize(results, unused_entries) -> str`, `result_code(results) -> int`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def t_surfaces_cover_all_four_uis():
    names = {s.name for s in v.SURFACES}
    for want in ("director-panel", "cockpit", "race-control"):
        assert want in names, want
    assert {f"cc-{view}" for view in v.CC_VIEWS} <= names
    assert len(v.CC_VIEWS) == 13, v.CC_VIEWS
    phone = {s.name for s in v.SURFACES if "phone" in s.viewports}
    assert phone == {"cockpit", "race-control"}, phone
    assert len({s.name for s in v.SURFACES}) == len(v.SURFACES), "surface names must be unique"


def t_surface_url_fills_tokens():
    urls = {"ui": "http://127.0.0.1:1", "relay": "http://127.0.0.1:2", "token": "T", "rc_token": "R"}
    by = {s.name: s for s in v.SURFACES}
    assert v.surface_url(by["race-control"], urls) == "http://127.0.0.1:2/console/race-control?t=R"
    assert v.surface_url(by["cockpit"], urls) == "http://127.0.0.1:2/cockpit?t=T"
    assert v.surface_url(by["cc-home"], urls) == "http://127.0.0.1:1/"


def t_report_shows_findings_escaped_and_verdicts():
    ok = v.SurfaceResult("cockpit", "phone", "cockpit-phone.png", [], 1, None)
    bad = v.SurfaceResult("director-panel", "desktop", "director-panel-desktop.png",
                          [v.Finding("contrast", "div > b<i>", "2.0:1 < 4.5:1")], 0, None)
    broken = v.SurfaceResult("race-control", "desktop", None, [], 0, "TimeoutError: ready")
    html = v.render_report([ok, bad, broken], [{"surface": "x", "rule": "overlap",
                                                 "selector": "#gone", "reason": "old"}])
    assert 'src="cockpit-phone.png"' in html
    assert "div &gt; b&lt;i&gt;" in html and "b<i>" not in html, "selectors must be escaped"
    assert html.count("FAIL") >= 2 and "PASS" in html
    assert "TimeoutError" in html and "#gone" in html, "errors and unused allowlist entries must show"


def t_result_code_and_summary():
    ok = v.SurfaceResult("a", "desktop", "a.png", [], 0, None)
    bad = v.SurfaceResult("b", "desktop", "b.png", [v.Finding("overflow", "html", "")], 0, None)
    assert v.result_code([ok]) == 0
    assert v.result_code([ok, bad]) == 1
    assert v.result_code([v.SurfaceResult("c", "phone", None, [], 0, "boom")]) == 1
    text = v.summarize([ok, bad], [])
    assert "[PASS] a desktop" in text and "[FAIL] b desktop" in text, text
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python3 tests/test_e2e_visual.py`
Expected: `AttributeError: module 'e2e_visual' has no attribute 'SURFACES'`

- [ ] **Step 3: Implement** (add `import html` at the top of `tools/e2e_visual.py`)

```python
VIEWPORTS = {"desktop": (1280, 800), "phone": (390, 844)}
SETTLE_MS = 800

Surface = collections.namedtuple("Surface", "name base path viewports prep ready timeout_ms")
SurfaceResult = collections.namedtuple(
    "SurfaceResult", "surface viewport shot findings suppressed error")

TALLY_READY = ("(() => { const t = document.getElementById('tally');"
               " return !!t && t.textContent.trim() !== '…'; })()")
CC_VIEWS = ("home", "streams", "services", "profile", "settings", "preflight", "tools",
            "apps", "console", "logs", "report", "help", "wizard")
CC_READY = "document.readyState === 'complete'"
PREFLIGHT_READY = ("(() => { const s = document.getElementById('pf-summary');"
                   " return !!s && !s.textContent.startsWith('running'); })()")

SURFACES = [
    Surface("director-panel", "relay", "/panel", ("desktop",), None,
            "!!document.querySelector('#schedBody tr')", 15000),
    Surface("cockpit", "relay", "/cockpit?t={token}", ("desktop", "phone"), None,
            TALLY_READY, 15000),
    Surface("race-control", "relay", "/console/race-control?t={rc_token}",
            ("desktop", "phone"), None, TALLY_READY, 15000),
] + [
    Surface(f"cc-{view}", "ui", "/", ("desktop",), f"showView('{view}')",
            PREFLIGHT_READY if view == "preflight" else CC_READY,
            90000 if view == "preflight" else 15000)
    for view in CC_VIEWS
]


def surface_url(surface, urls):
    """Absolute URL of *surface*; *urls* holds the ui/relay base URLs and the two tokens."""
    return urls[surface.base] + surface.path.format(token=urls["token"], rc_token=urls["rc_token"])


def result_code(results):
    """1 when any surface has a finding or failed to render, else 0."""
    return 1 if any(r.findings or r.error for r in results) else 0


def summarize(results, unused_entries):
    lines = []
    for r in results:
        mark = "FAIL" if (r.findings or r.error) else "PASS"
        tail = r.error or (f"{len(r.findings)} finding(s)" if r.findings else "")
        lines.append(f"  [{mark}] {r.surface} {r.viewport}" + (f": {tail}" if tail else ""))
        for f in r.findings:
            lines.append(f"      {f.rule} {f.selector}: {f.detail}")
    for e in unused_entries:
        lines.append(f"  [WARN] unused allowlist entry: {e['surface']} {e['rule']} {e['selector']}")
    bad = sum(1 for r in results if r.findings or r.error)
    lines.append(f"  {len(results) - bad} surface views clean, {bad} with findings")
    return "\n".join(lines)


_REPORT_CSS = """
:root { --bg:#f6f7f9; --ink:#14171c; --card:#fff; --line:#d8dce3; --bad:#b42318; --ok:#1a7f37; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1216; --ink:#e6e9ee; --card:#171b21; --line:#2a313b; --bad:#ff7b72; --ok:#56d364; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }
section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; margin:0 0 16px; }
h2 { font-size:16px; margin:0 0 8px; } .FAIL { color:var(--bad); } .PASS { color:var(--ok); }
img { max-width:100%; border:1px solid var(--line); } code { font-size:12px; }
table { border-collapse:collapse; width:100%; } td { border-top:1px solid var(--line); padding:4px; vertical-align:top; }
"""


def render_report(results, unused_entries):
    """A self-contained HTML report: verdict, findings and screenshot per surface view."""
    esc = html.escape
    parts = ["<!doctype html><html lang=en><meta charset=utf-8>"
             "<meta name=viewport content='width=device-width, initial-scale=1'>"
             f"<title>Visual acceptance</title><style>{_REPORT_CSS}</style><body>",
             f"<h1>Visual acceptance</h1><pre>{esc(summarize(results, unused_entries))}</pre>"]
    for r in results:
        verdict = "FAIL" if (r.findings or r.error) else "PASS"
        parts.append(f"<section><h2><span class={verdict}>{verdict}</span> "
                     f"{esc(r.surface)} <small>{esc(r.viewport)}</small></h2>")
        if r.error:
            parts.append(f"<p class=FAIL>{esc(r.error)}</p>")
        if r.findings:
            rows = "".join(f"<tr><td>{esc(f.rule)}</td><td><code>{esc(f.selector)}</code></td>"
                           f"<td>{esc(f.detail)}</td></tr>" for f in r.findings)
            parts.append(f"<table>{rows}</table>")
        if r.suppressed:
            parts.append(f"<p>{r.suppressed} allowlisted finding(s) hidden</p>")
        if r.shot:
            parts.append(f'<img src="{esc(r.shot)}" alt="{esc(r.surface)} {esc(r.viewport)}" loading=lazy>')
        parts.append("</section>")
    if unused_entries:
        items = "".join(f"<li><code>{esc(e['surface'])} {esc(e['rule'])} {esc(e['selector'])}</code>"
                        f" {esc(e['reason'])}</li>" for e in unused_entries)
        parts.append(f"<section><h2>Unused allowlist entries</h2><ul>{items}</ul></section>")
    parts.append("</body></html>")
    return "\n".join(parts)
```

- [ ] **Step 4: Run, expect PASS**

Run: `python3 tests/test_e2e_visual.py`
Expected: `PASS test_e2e_visual`

- [ ] **Step 5: Lint and commit**

```bash
python3 tools/lint.py
git add tools/e2e_visual.py tests/test_e2e_visual.py
git commit -m "feat(e2e): define the visual-run surfaces and render its report

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Browser probe and the `--visual` driver

**Files:**
- Create: `tools/visual-probe.js`
- Modify: `tools/e2e.py` (imports, new `run_visual`/`_visual_one`, `run_synthetic` step 7, `main`)

**Interfaces:**
- Consumes: everything from Tasks 1 to 5.
- Produces: CLI `tools/e2e.py --visual [--report DIR]`; exit code 0 clean, 1 on a finding or a broken surface, 2 when Playwright is missing.

- [ ] **Step 1: Set up Playwright locally** (one time, kept under the gitignored `runtime/`)

```bash
python3 -m venv runtime/pw-venv
runtime/pw-venv/bin/pip install playwright==1.63.0
runtime/pw-venv/bin/python -m playwright install chromium
```

On Windows the interpreter is `runtime\pw-venv\Scripts\python.exe`. All later runs in this task use `runtime/pw-venv/bin/python tools/e2e.py ...`.

- [ ] **Step 2: Write the probe** (`tools/visual-probe.js`; one function expression, passed to `page.evaluate`)

```js
() => {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "BR", "OPTION", "IFRAME"]);
  const INTERACTIVE = "button, input, select, textarea, a[href], [role=button], [tabindex]:not([tabindex='-1'])";
  const NO_UA_CHECK = new Set(["checkbox", "radio", "range", "color", "file", "hidden", "image"]);
  const doc = document.documentElement;

  const rgba = s => {
    const m = /^rgba?\(([^)]+)\)$/.exec(s || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };

  const bgChain = el => {
    const chain = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const c = rgba(cs.backgroundColor);
      if (c === null) return {chain, image: true};
      // The colour under a gradient still counts, so a body with gradients over a dark base reads as dark.
      if (c[3] > 0) chain.push(c);
      if (cs.backgroundImage !== "none") return {chain, image: true};
      if (c[3] >= 1) break;
    }
    return {chain, image: false};
  };

  const opacity = el => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity);
    return o;
  };

  const selector = el => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== doc && parts.length < 4; e = e.parentElement) {
      if (e.id) { parts.unshift("#" + CSS.escape(e.id)); break; }
      let s = e.tagName.toLowerCase();
      const cls = [...e.classList].slice(0, 2);
      if (cls.length) s += "." + cls.map(c => CSS.escape(c)).join(".");
      const sib = e.parentElement ? [...e.parentElement.children].filter(x => x.tagName === e.tagName) : [];
      if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(e) + 1})`;
      parts.unshift(s);
    }
    return parts.join(" > ");
  };

  const ownText = el => [...el.childNodes].filter(n => n.nodeType === 3)
    .map(n => n.textContent).join("").trim().slice(0, 60);

  // UA defaults come from bare controls in an unstyled same-origin iframe.
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:absolute;left:-9999px;top:0;width:10px;height:10px";
  document.body.appendChild(frame);
  const fd = frame.contentDocument;
  fd.open(); fd.write("<!doctype html><input><select></select><textarea></textarea><button>x</button>"); fd.close();
  const ua = {};
  for (const tag of ["input", "select", "textarea", "button"]) {
    ua[tag] = frame.contentWindow.getComputedStyle(fd.querySelector(tag)).backgroundColor;
  }
  frame.remove();

  const scrollWidth = doc.scrollWidth;
  const index = new Map();
  const elements = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (SKIP.has(el.tagName) || el.closest("svg")) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (!el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    const x = r.left + scrollX, y = r.top + scrollY;
    if (x + r.width <= 0 || y + r.height <= 0 || x >= scrollWidth) continue;
    const cs = getComputedStyle(el);
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.type || "text") : "";
    let uaKey = null;
    if (tag === "input" && !NO_UA_CHECK.has(type)) uaKey = ["submit", "button", "reset"].includes(type) ? "button" : "input";
    else if (["select", "textarea", "button"].includes(tag)) uaKey = tag;
    const bg = bgChain(el);
    const rec = {
      i: elements.length, sel: selector(el), tag, interactive: el.matches(INTERACTIVE),
      text: ownText(el), has_text: (el.textContent || "").trim() !== "",
      rect: [x, y, r.width, r.height],
      client_w: el.clientWidth, scroll_w: el.scrollWidth,
      client_h: el.clientHeight, scroll_h: el.scrollHeight,
      overflow_x: cs.overflowX, overflow_y: cs.overflowY, text_overflow: cs.textOverflow,
      line_clamp: !!cs.webkitLineClamp && cs.webkitLineClamp !== "none",
      color: rgba(cs.color), bg_chain: bg.chain, bg_image: bg.image, opacity: opacity(el),
      font_size: parseFloat(cs.fontSize), font_weight: parseInt(cs.fontWeight, 10) || 400,
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
      ua_key: uaKey, bg_raw: cs.backgroundColor,
      parent_bg: el.parentElement ? bgChain(el.parentElement).chain : [],
      anc: [],
    };
    if (rec.interactive) {
      for (let p = el.parentElement; p; p = p.parentElement) if (index.has(p)) rec.anc.push(index.get(p));
      index.set(el, rec.i);
    }
    elements.push(rec);
  }
  return {viewport: {w: doc.clientWidth, h: innerHeight}, scroll_width: scrollWidth, ua, elements};
}
```

Syntax check: `node --check tools/visual-probe.js` (the file is one expression statement, so it parses).

- [ ] **Step 3: Add the driver to `tools/e2e.py`**

Imports next to the existing `import e2e_checks as E`: `import e2e_visual as V`. Constants below `CREW_ROWS`:

```python
PROBE_JS = os.path.join(ROOT, "tools", "visual-probe.js")
ALLOWLIST = os.path.join(ROOT, "tools", "visual-allowlist.json")
PLAYWRIGHT_HINT = ("pip install playwright==1.63.0 && python -m playwright install chromium "
                   "(see tools/CLAUDE.md)")
```

Below `_capture_shots`:

```python
def _visual_one(browser, surface, viewport, urls, outdir, probe, allow, used):
    """Render one surface in one viewport, screenshot it and judge it. Returns a SurfaceResult."""
    w, h = V.VIEWPORTS[viewport]
    shot = f"{surface.name}-{viewport}.png"
    errors = []

    def on_console(msg):
        if msg.type == "error":
            errors.append(f"console.error: {msg.text}")

    page = browser.new_page(viewport={"width": w, "height": h})
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", on_console)
    try:
        page.goto(V.surface_url(surface, urls), wait_until="domcontentloaded")
        if surface.prep:
            page.evaluate(surface.prep)
        page.wait_for_function(surface.ready, timeout=surface.timeout_ms)
        page.wait_for_timeout(V.SETTLE_MS)
        page.screenshot(path=os.path.join(outdir, shot), full_page=True)
        facts = page.evaluate(probe)
    except Exception as exc:  # noqa: BLE001  a surface that does not render is a failure
        return V.SurfaceResult(surface.name, viewport, None, [], 0, f"{type(exc).__name__}: {exc}")
    finally:
        page.close()
    findings = V.evaluate(facts, errors)
    kept, hit = V.apply_allowlist(findings, allow, surface.name, viewport)
    used.update(hit)
    return V.SurfaceResult(surface.name, viewport, shot, kept, len(findings) - len(kept), None)


def run_visual(urls, outdir, headed=False, slowmo=0):
    """Render every surface of the visual acceptance run, write DIR/report.html and the
    screenshots, print a summary. Returns 0 when clean, 1 on a finding or a broken surface."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    with open(PROBE_JS, encoding="utf-8") as fh:
        probe = fh.read()
    allow = V.load_allowlist(ALLOWLIST)
    os.makedirs(outdir, exist_ok=True)
    results, used = [], set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
        try:
            for surface in V.SURFACES:
                for viewport in surface.viewports:
                    results.append(_visual_one(browser, surface, viewport, urls, outdir,
                                               probe, allow, used))
        finally:
            browser.close()
    unused = [e for n, e in enumerate(allow) if n not in used]
    report = os.path.join(outdir, "report.html")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(V.render_report(results, unused))
    print("visual acceptance:\n" + V.summarize(results, unused))
    print(f"--visual: report at {report}")
    return V.result_code(results)
```

In `run_synthetic`, step 7, insert directly after `print(E.summarize(results))`, so the API summary prints first:

```python
        if args.visual:
            if not _playwright_available():
                print(f"--visual: Playwright + Chromium required: {PLAYWRIGHT_HINT}")
                return 2
            urls = {"ui": ui_url, "relay": relay_url, "token": token, "rc_token": rc_token}
            code = max(code, run_visual(urls, args.report, headed=args.headed, slowmo=args.slowmo))
```

In `main`, next to `--shots`:

```python
    ap.add_argument("--visual", action="store_true",
                    help="visual acceptance run: render Control Center, Director Panel, cockpit "
                         "and Race Control, apply the layout rules, fail on a finding "
                         "(synthetic mode only, needs Playwright)")
    ap.add_argument("--report", metavar="DIR",
                    default=os.path.join(ROOT, "runtime", "visual-report"),
                    help="where --visual writes report.html and the screenshots "
                         "(default runtime/visual-report)")
```

and before `return run_real_league(args)`:

```python
        if args.visual:
            ap.error("--visual is synthetic-only; not supported with --real-league")
```

- [ ] **Step 4: First run**

Run: `runtime/pw-venv/bin/python tools/e2e.py --visual`
Expected: the API checks pass as before, then one line per surface view (13 Control Center views, panel, cockpit x2, Race Control x2 = 18), then `--visual: report at .../runtime/visual-report/report.html`. Findings are expected on this first run; Task 7 handles them. What must hold now: no surface ends in `TimeoutError` or another render error. Open `report.html` and look at every screenshot: the panel shows the schedule and a program still, the cockpit and Race Control show the tally, Race Control is the desk and not a 403 page.

A `TimeoutError` on a surface means its `ready` expression never turned true. Open the page with `--keep` in a browser, find a selector that appears once data arrived, and change that surface's `ready` in `tools/e2e_visual.py`; then rerun `python3 tests/test_e2e_visual.py`.

- [ ] **Step 5: Prove the run catches #397** (temporary, restored from a stash snapshot)

```bash
snap=$(git stash create)
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/director/director-panel.html")
p.write_text(p.read_text(encoding="utf-8").replace(
    "</body>", '<input id="probe397" style="all:revert;position:fixed;left:8px;bottom:8px"></body>', 1),
    encoding="utf-8")
PY
runtime/pw-venv/bin/python tools/e2e.py --visual; echo "exit=$?"
git restore --source="${snap:-HEAD}" -- src/director/director-panel.html
git diff --stat src/director/director-panel.html
```

Expected: `unstyled-control #probe397` under `director-panel desktop`, `exit=1`, and an empty diff afterwards. The Stop hook watches this file: since the edit is reverted before the turn ends, record it as "needs no render" per the `ui-visual-verification` skill if the hook still asks.

- [ ] **Step 6: Lint, test, commit**

```bash
python3 tools/lint.py && python3 tests/test_e2e_visual.py && python3 tests/test_e2e.py
git add tools/visual-probe.js tools/e2e.py
git commit -m "feat(e2e): add the --visual acceptance run

A browser probe turns each rendered surface into element facts, the rules in
e2e_visual.py judge them, and DIR/report.html shows verdicts, findings and
screenshots. Covers 13 Control Center views, the Director Panel, the cockpit
and the Race Control desk; the two crew pages also at phone width.

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Triage the first run

The size of this task is unknown until Task 6 runs. Keep the PR about the harness: UI fixes go to their own issues.

**Files:**
- Modify: `tools/visual-allowlist.json`, possibly `tools/e2e_visual.py` (tolerances, `ready` expressions)

- [ ] **Step 1: Sort every finding into one of three buckets**

1. **Rule false positive** (the page is fine, the rule misreads it, e.g. a decorative overlay counted as overlap): fix the rule or the probe, add a unit test that reproduces the false positive in `tests/test_e2e_visual.py` first.
2. **Deliberate design** (e.g. a dimmed "idle" label meant to recede): allowlist entry with a reason that names the design intent.
3. **Real UI defect**: open a GitHub issue per surface (label `bug`, title `<Surface>: <rule> findings from the visual acceptance run`, body lists selector, viewport and detail, links #772), then allowlist the finding with `"reason": "known defect, #<new issue>"`. Do not fix it in this PR.

Before creating more than three issues, show the user the list and ask whether to batch them.

Expect an `overflow` finding on `director-panel desktop`: its drawer sits at `translateX(100%)` and a transformed box widens `scrollWidth` unless an ancestor clips it. Check in the screenshot whether the page really scrolls sideways (bucket 3) or the drawer only widens the measurement (bucket 1, then exclude elements whose computed transform moves them fully outside the viewport, with a unit test).

- [ ] **Step 2: Rerun until clean**

Run: `runtime/pw-venv/bin/python tools/e2e.py --visual; echo "exit=$?"`
Expected: `exit=0`, no `[WARN] unused allowlist entry`.

- [ ] **Step 3: Check stability**

Run the command from Step 2 three times in a row. All three must end with `exit=0`. A finding that comes and goes is timing: raise `SETTLE_MS` or tighten that surface's `ready`, never allowlist flakiness.

- [ ] **Step 4: Lint, test, commit**

```bash
python3 tools/lint.py && python3 tests/test_e2e_visual.py
git add tools/visual-allowlist.json tools/e2e_visual.py tests/test_e2e_visual.py
git commit -m "chore(e2e): triage the first visual acceptance run

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: CI job and docs

**Files:**
- Modify: `.github/workflows/ci.yml` (new job after `e2e`)
- Modify: `tools/CLAUDE.md` (e2e section and the maintainer command block)
- Modify: `.claude/skills/ui-visual-verification/SKILL.md`

- [ ] **Step 1: Add the job** (after the `e2e` job, same indentation)

```yaml
  visual:
    # Visual acceptance run: renders Control Center, Director Panel, cockpit and
    # Race Control in Chromium and fails on a layout-rule finding. The stdlib e2e
    # job above stays Playwright-free.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0
        with:
          python-version: "3.12"
      - name: Install Playwright + Chromium
        run: |
          python -m pip install playwright==1.63.0
          python -m playwright install --with-deps chromium
      - name: Run the visual acceptance run
        run: python tools/e2e.py --visual --report visual-report
      - name: Upload the report
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        with:
          name: visual-report
          path: visual-report/
          if-no-files-found: ignore
```

If branch protection lists required checks (`gh api repos/{owner}/{repo}/branches/main/protection -q '.required_status_checks.contexts'`), tell the user that `visual` is not required yet and leave the setting to them.

- [ ] **Step 2: Document the run in `tools/CLAUDE.md`**

After the paragraph that describes `--shots`, add:

```markdown
**Visual acceptance run** (`--visual [--report DIR]`, #772): renders the 13 Control Center
views, the Director Panel, the cockpit and the Race Control desk (the two crew pages also at
390 px) against the synthetic run, which serves a Crew roster (`--crew-csv-url`) and runs
`tools/obs-sim.py`. `tools/visual-probe.js` reads element facts in the page;
`tools/e2e_visual.py` applies six rules (page overflow, clipped text, a control left at the
browser default on a dark surface, overlapping controls, WCAG AA contrast, console errors)
and writes `DIR/report.html` plus screenshots. Any finding fails the run (exit 1); a missing
Playwright is exit 2, never a skip. Deliberate exceptions go in `tools/visual-allowlist.json`,
each with a reason; a real defect gets its own issue and an allowlist entry pointing at it.
The CI `visual` job runs it on every PR and uploads the report. Local setup:
`python3 -m venv runtime/pw-venv && runtime/pw-venv/bin/pip install playwright==1.63.0 &&
runtime/pw-venv/bin/python -m playwright install chromium`.
```

In the maintainer command block add:

```bash
runtime/pw-venv/bin/python tools/e2e.py --visual   # visual acceptance run -> runtime/visual-report/report.html
```

- [ ] **Step 3: Point the skill at it**

In `.claude/skills/ui-visual-verification/SKILL.md`, at the end of "The procedure", add:

```markdown
### Then run the automated rules
`runtime/pw-venv/bin/python tools/e2e.py --visual` (setup in `tools/CLAUDE.md`) checks every
surface for overflow, clipped text, browser-default controls, overlaps, contrast and console
errors. It does not replace looking: it catches what a quick look misses, and the CI `visual`
job runs it anyway. Your element screenshot still decides whether the change looks right.
```

Run `python3 tests/test_skill_recipes.py` afterwards.

- [ ] **Step 4: Full local gates**

```bash
python3 tools/lint.py
python3 tools/run-tests.py
python3 tools/build.py
python3 tools/e2e.py
runtime/pw-venv/bin/python tools/e2e.py --visual
```

Expected: all green, the stdlib run still prints the new `race_control_page` and `program_monitor` passes.

- [ ] **Step 5: Commit, sync with main, open the PR**

```bash
git add .github/workflows/ci.yml tools/CLAUDE.md .claude/skills/ui-visual-verification/SKILL.md
git commit -m "ci: run the visual acceptance run on every PR

Refs #772

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git fetch origin && git rebase origin/main
git push -u origin feat/772-visual-acceptance
gh pr create --title "feat(e2e): automated visual acceptance run for the four operator UIs" \
  --body "Closes #772. <summary of rules, surfaces, CI job, triage outcome and follow-up issues>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Fill the summary from the actual triage outcome. Watch CI; when the `visual` job fails only in CI, download the `visual-report` artifact (`gh run download <run-id> -n visual-report`) and treat its findings like Task 7 (font differences on the runner are the likely cause). Merge per the `ship-feature` skill after green CI.

---

## Self-review

- Issue coverage: surfaces (Task 5), viewports (Task 5), obs-sim data (Task 2), Race Control reachability (Task 1), six rules (Tasks 3, 4), allowlist with reasons (Task 4), HTML report (Task 5, 6), CI job with artifact (Task 8), unit tests incl. #397 (Task 3, 6 Step 5), docs (Task 8).
- Deviation from the issue text: the rules live in a new `tools/e2e_visual.py` instead of `tools/e2e_checks.py`, which stays the HTTP-check module. The issue is updated to match.
- Open risk: Chromium fonts on `ubuntu-latest` differ from a desktop, so clipped-text and contrast findings may differ between local and CI. Task 8 Step 5 covers it.
