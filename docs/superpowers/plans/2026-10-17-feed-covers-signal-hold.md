# Feed covers, signal hold and ultra-low-latency hint: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remote feeds never show black when OBS (re)opens them, and never stutter on air. A current still of the feed covers both cases (muted, with a Sheet notice during a source stall). The Director Panel also flags ultra-low-latency YouTube streams before they go on air.

**Architecture:** All runtime logic lives in the relay (`src/relay/racecast-feeds.py`). Each decision is a pure function, unit-tested with tables. A 1 s relay thread applies those decisions through `obs_ws` (`src/scripts/obs_ws.py`) on two new OBS image inputs that the relay creates itself. Health events go through the existing health store; the post-event report (`src/scripts/report_build.py`) renders them.

**Tech Stack:** Python 3.11+ stdlib only, ffmpeg (already a runtime dep), obs-websocket v5, stdlib test scripts under `tests/`.

**Spec:** `docs/superpowers/specs/2026-10-17-feed-signal-hold-design.md` (revision 2). Issues: #918 (Parts A and B), #919 (Part C).

## Global Constraints

- Edit only under `src/` (plus `tests/`, `tools/` and docs). English-only code, comments and docs. No machine paths, no real IPs in tests.
- Relay scripts stay self-contained: `src/relay/racecast-feeds.py` may use `urllib` directly (root CLAUDE.md exception). It must not import new shared modules beyond the ones it already imports.
- Every text-mode `subprocess` call passes `errors="replace"`; ffmpeg here is binary-mode (stdout bytes), as in `_PreviewRingTap`.
- **No extra delay and no quality step-down:** `RACECAST_FEED_PREBUFFER_S`, `--hls-live-edge` and the tier logic must not change (#619).
- `reflect_feed_state` aborts on a missing scene item, so **cover items must never be added to `feed_state_intents` or `split_state_intents`**.
- Best-effort OBS contract: every new `obs_ws` helper returns `(value, note)` and never raises. Every relay OBS call is wrapped so a failure only sets `self.obs_note`.
- One PR per issue, each with a conventional title:
  - PR 1, Part A: "Part of #918";
  - PR 2, Part C: "Closes #919";
  - PR 3, Part B: "Closes #918".
- Commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Arm auto-merge (squash).
- UI surfaces changed (`src/director/director-panel.html`): render and look (ui-visual-verification), refresh `src/docs/wiki/images/director-panel.png` in the same PR.
- Local gates before each PR:
  - `python tools/run-tests.py`
  - `python tools/lint.py`
  - `python tools/build.py`
  - `python tests/test_pov.py`, because these are relay changes.
- **Not during a live event:** these gates and the e2e harness start relays on fixed ports. Check that no relay or stream is running on the producer machine first.

## Review Focus

1. **A cover above a hidden feed item would hide the on-air picture in the Stint scene.** Expected: a cover is never shown while its feed item is disabled in that scene. Test: `t_cover_hidden_when_feed_item_hidden` (Task 6).
2. **Cover items are missing (old OBS collection, OBS down at relay start).** Expected: no exception, `/status` `cover: "missing"`, a retry every 30 s, and handovers unaffected. Test: `t_cover_tick_missing_items_never_raise` (Task 7).
3. **A handover or RESET during a hold.** Expected: the hold cancels, the mute is restored only if the hold set it, and the notice is cleared. Test: `t_hold_cancel_restores_only_our_mute` (Task 10).
4. **A director muted the feed before the hold.** Expected: the release does not unmute it. Test: `t_hold_release_keeps_director_mute` (Task 10).
5. **The Signal Hold column is missing or empty.** Expected: the hold still covers and mutes, and the HUD keeps the Sheet's Race Control text. Test: `t_hud_notice_absent_text_keeps_sheet_value` (Task 11).

---

# PR 1: Part A, ultra-low-latency hint (Part of #918)

Branch: `feat/feed-ull-hint` off fresh `origin/main`.

### Task 1: Measure the YouTube "Low latency" target duration (manual, gate)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-17-feed-signal-hold-design.md` (record the value)

- [ ] **Step 1: Get a Low-latency test stream.** Ask the producer for a YouTube live URL streamed with **Low latency** (not Ultra-low), or a test stream they start. Do not guess one.
- [ ] **Step 2: Fetch its target duration.**

```bash
python - "<URL>" <<'EOF'
import subprocess, sys, urllib.request
hls = subprocess.run(["yt-dlp", "-g", "-f", "b[height<=1080]/b", sys.argv[1]],
                     capture_output=True, text=True, errors="replace").stdout.strip().splitlines()[0]
body = urllib.request.urlopen(hls, timeout=10).read(65536).decode("utf-8", "replace")
print([l for l in body.splitlines() if l.startswith(("#EXT-X-TARGETDURATION", "#EXT-X-STREAM-INF"))][:3])
EOF
```

Expected: `#EXT-X-TARGETDURATION:2` (or any value > 1).

- [ ] **Step 3: Decide.**
  - If the value is > 1, record it in the spec's Part A "Threshold" paragraph ("Measured 2026-10-xx: Low latency = N s") and continue.
  - **If it is 1, stop here and report to the producer.** The hint cannot separate Low from Ultra-low.
- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/specs/2026-10-17-feed-signal-hold-design.md
git commit -m "docs(spec): record the measured YouTube low-latency target duration"
```

### Task 2: Probe the manifest's target duration in the relay

**Files:**
- Modify: `src/relay/racecast-feeds.py`:
  - `ssai_warning`, around line 3729;
  - the serve path around line 8159;
  - `Feed.__init__`, around line 7740;
  - `Relay.status`, around line 9380.
- Create: `tests/test_signal_hold.py`

**Interfaces:**
- Produces:
  - `ULL_TARGET_DURATION_S = 1.0`;
  - `parse_target_duration(text, base_url) -> tuple[float | None, str | None]`, which returns (duration, first variant URL when `text` is a master playlist);
  - `probe_manifest(hls_url, logger, get=None) -> tuple[str | None, float | None]`, which returns (SSAI warning, target duration);
  - `is_ultra_low_latency(target_s) -> bool`;
  - `Feed.target_duration_s`;
  - `/status` `feeds.<X>.target_duration_s` and `feeds.<X>.ultra_low_latency`.

- [ ] **Step 1: Create the test file with failing tests.**

```python
#!/usr/bin/env python3
"""Feed covers, signal hold and ultra-low-latency hint (spec 2026-10-17).
Run: python3 tests/test_signal_hold.py"""
import importlib.util, os, logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
LOG = logging.getLogger("t")

MEDIA_ULL = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:5856\n#EXTINF:1.0,\nseg.ts\n"
MEDIA_NORMAL = "#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5.0,\nseg.ts\n"
MASTER = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n"
          "variant/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3000000\nlow.m3u8\n")


def t_parse_target_duration_media_playlist():
    assert m.parse_target_duration(MEDIA_ULL, "https://h/x.m3u8") == (1.0, None)
    assert m.parse_target_duration(MEDIA_NORMAL, "https://h/x.m3u8") == (5.0, None)


def t_parse_target_duration_master_points_at_first_variant():
    assert m.parse_target_duration(MASTER, "https://h/a/master.m3u8") == (
        None, "https://h/a/variant/index.m3u8")


def t_parse_target_duration_garbage_is_none():
    for text in ("", None, "#EXTM3U\n", "#EXT-X-TARGETDURATION:abc\n"):
        assert m.parse_target_duration(text, "https://h/x") == (None, None), text


def t_probe_manifest_follows_one_master_hop():
    pages = {"https://h/a/master.m3u8": MASTER, "https://h/a/variant/index.m3u8": MEDIA_ULL}
    warn, target = m.probe_manifest("https://h/a/master.m3u8", LOG, get=pages.__getitem__)
    assert (warn, target) == (None, 1.0)


def t_probe_manifest_failure_is_quiet():
    def boom(_url):
        raise OSError("down")
    assert m.probe_manifest("https://h/x", LOG, get=boom) == (None, None)


def t_probe_manifest_keeps_the_ssai_warning():
    ssai = MEDIA_NORMAL + "#EXT-X-DATERANGE:ID=\"ad\",CLASS=\"com.apple.hls.interstitial\"\n"
    warn, target = m.probe_manifest("https://h/x", LOG, get=lambda _u: ssai)
    assert target == 5.0 and (warn is None or "server-side ads" in warn)


def t_is_ultra_low_latency_threshold():
    assert m.is_ultra_low_latency(1.0) is True
    assert m.is_ultra_low_latency(2.0) is False
    assert m.is_ultra_low_latency(None) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

Note: `t_probe_manifest_keeps_the_ssai_warning` accepts either result, because the SSAI marker regex `_SSAI_RE` decides. Before running, read `_SSAI_RE` and replace the fixture line with a marker it matches, then assert `"server-side ads" in warn` strictly.

- [ ] **Step 2: Run it and check that it fails.**

Run: `python tests/test_signal_hold.py`
Expected: `AttributeError: module 'irofeeds' has no attribute 'parse_target_duration'`

- [ ] **Step 3: Implement.** Next to `ssai_warning` in `src/relay/racecast-feeds.py`, replace `ssai_warning` with:

```python
# Ultra-low-latency YouTube streams cut 1 s HLS segments; Low latency measured <N> s
# and normal latency 5 s (2026-09-21). Spec 2026-10-17-feed-signal-hold-design.
ULL_TARGET_DURATION_S = 1.0
_TARGET_RE = re.compile(r"^#EXT-X-TARGETDURATION:\s*([0-9.]+)\s*$", re.M)


def parse_target_duration(text, base_url):
    """HLS playlist text -> (target duration s, None) for a media playlist, or
    (None, first variant URL) for a master playlist. (None, None) for anything else.
    Pure."""
    if not text:
        return None, None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("#"):
                    return None, urllib.parse.urljoin(base_url, nxt.strip())
            return None, None
    mt = _TARGET_RE.search(text)
    if not mt:
        return None, None
    try:
        return float(mt.group(1)), None
    except ValueError:
        return None, None


def is_ultra_low_latency(target_s):
    return target_s is not None and target_s <= ULL_TARGET_DURATION_S


def _get_manifest(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=10) as r:   # noqa: S310  https HLS only
        return r.read(65536).decode("utf-8", errors="replace")


def probe_manifest(hls_url, logger, get=None):
    """Fetch the resolved manifest once (following one master hop) and return
    (ssai warning or None, target duration s or None). Best effort: any failure
    returns (None, None), so the feed is never blocked by the probe."""
    get = get or _get_manifest
    try:
        body = get(hls_url)
        target, variant = parse_target_duration(body, hls_url)
        if variant:
            body = get(variant)
            target, _ = parse_target_duration(body, variant)
    except Exception:
        return None, None
    warning = None
    if manifest_has_ssai_markers(body):
        logger.warning("source manifest carries server-side ads (cannot strip)")
        warning = "source has server-side ads (not a clean broadcast feed)"
    if is_ultra_low_latency(target):
        logger.warning("source is ultra-low latency (%.0f s segments): stall-prone", target)
    return warning, target
```

Check that `re` and `urllib.parse` are imported at module top (`grep -n "^import\|^from" src/relay/racecast-feeds.py`). Add `import urllib.parse` there if missing.

Then:
- In `Feed.__init__`, add next to `self.quality = None`:
  ```python
  self.target_duration_s = None   # HLS #EXT-X-TARGETDURATION of the current serve (spec 2026-10-17)
  ```
- At the serve site (line ~8159), replace `self.last_error = ssai_warning(hls, self.log)` with:
  ```python
  self.last_error, self.target_duration_s = probe_manifest(hls, self.log)  # warn, never block
  ```
- Reset `self.target_duration_s = None` wherever the Twitch/local branch sets `target` (the non-YouTube serve), so a stale value never carries over.
- Update every remaining `ssai_warning(` caller: `grep -n "ssai_warning(" src tests tools`. Keep a thin `ssai_warning` wrapper only if a test imports it.

- [ ] **Step 4: Publish in `/status`.** In `Relay.status`, inside the `out["feeds"][k] = {...}` dict, add:

```python
"target_duration_s": f.target_duration_s,
"ultra_low_latency": is_ultra_low_latency(f.target_duration_s),
```

Add a test that builds the status dict entry. If `Relay.status` is too heavy to construct, cover it in the e2e (Task 3, Step 4) and keep this unit-level only.

- [ ] **Step 5: Run the tests.** `python tests/test_signal_hold.py && python tests/test_pov.py && python tests/test_fanout.py` must all pass.
- [ ] **Step 6: Commit.** `feat(relay): read the HLS target duration of each remote serve`

### Task 3: Director Panel hint

**Files:**
- Modify: `src/director/director-panel.html` (`statePill` calls around line 2116)
- Modify: `tools/e2e.py`, or the panel test that renders `statePill`: find it with `grep -ln "statePill" tests tools`
- Modify: `src/docs/wiki/Director.md` (or the page that documents the A/B pills: `grep -ln "BEHIND LIVE" src/docs/wiki`)
- Modify: `src/docs/wiki/images/director-panel.png`

- [ ] **Step 1: Failing test.** In the test that covers the strip pills (or a new node-based check like `tests/test_engineer_live_trend.py` uses), assert:
  - a feed with `ultra_low_latency: true` renders its pill with class `warn`;
  - its text ends in `ULL`;
  - its title contains `Low latency`.
- [ ] **Step 2: Implement.** After the two `statePill(...)` calls in `relayPoll`:

```js
for (const k of ["A", "B"]) {
  const f = d.feeds[k], el = $("#st" + k);
  if (f && f.ultra_low_latency) {
    el.classList.add("warn");
    el.querySelector("b").textContent += " · ULL";
    el.title = "Ultra-low latency (" + f.target_duration_s + " s segments): stall-prone. "
             + "Ask the commentator to switch to Low latency.";
  }
}
```

  `statePill` rewrites the pill every poll, so the suffix never accumulates. Check that in `statePill`'s source before relying on it.
- [ ] **Step 3: Render and look** (ui-visual-verification skill). Recapture `director-panel.png` with the wiki-screenshots skill, then record the marker.
- [ ] **Step 4: Docs.** One paragraph in the wiki pill section: what `ULL` means and what to ask the commentator. No crew rule.
- [ ] **Step 5: Gates, commit, PR.**
  - Title: `feat(relay): flag ultra-low-latency feeds in the Director Panel`.
  - Body: "Part of #918", with the measured Low-latency value.

---

# PR 2: Part C, feed covers (Closes #919)

Branch: `feat/feed-covers` off fresh `origin/main` (after PR 1 merged).

### Task 4: obs_ws helpers for covers

**Files:**
- Modify: `src/scripts/obs_ws.py`:
  - constants near `FEED_SOURCES`, line 44;
  - new helpers after `set_scene_item_transform`, line 1610;
  - `_ROUTED_FNS`, line 575.
- Test: `tests/test_obsws.py` (uses its `_FakeSession`)

**Interfaces:**
- Produces:
  - `COVER_SOURCES = {"A": "Feed A Cover", "B": "Feed B Cover"}`;
  - `COVER_SCENES = (STINT_SCENE, SPLIT_SCENE)`;
  - `ensure_cover_items(feed_source, cover_source, cover_file, scenes=COVER_SCENES, session=None) -> (dict[scene, "ok"|"created"|"no-feed"|"error: ..."], note)`;
  - `get_scene_item_enabled(scene, source, session=None) -> (bool | None, note)`;
  - `set_input_file(input_name, path, session=None) -> (bool, note)`.

- [ ] **Step 1: Failing tests** (append to `tests/test_obsws.py`):

```python
def t_ensure_cover_items_creates_input_and_items_above_the_feed():
    s = _FakeSession({
        "GetInputList": {"inputs": [{"inputName": "Feed A"}]},
        "GetSceneItemList": {"sceneItems": [
            {"sourceName": "Feed A", "sceneItemId": 3, "sceneItemIndex": 1},
            {"sourceName": "Overlay", "sceneItemId": 9, "sceneItemIndex": 2}]},
        "GetSceneItemTransform": {"sceneItemTransform": {"positionX": 0.0, "positionY": 0.0,
                                  "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 960.0,
                                  "boundsHeight": 540.0, "alignment": 5, "boundsAlignment": 0,
                                  "sourceWidth": 1920.0, "width": 960.0}},
        "CreateInput": {"sceneItemId": 11}, "CreateSceneItem": {"sceneItemId": 12}})
    report, note = m.ensure_cover_items("Feed A", "Feed A Cover", "/tmp/a.jpg",
                                        scenes=("Stint", "Splitscreen"), session=s)
    kinds = [k for k, _ in s.sent]
    assert report == {"Stint": "created", "Splitscreen": "created"}, (report, note)
    assert kinds.count("CreateInput") == 1 and kinds.count("CreateSceneItem") == 1
    idx = [d for k, d in s.sent if k == "SetSceneItemIndex"]
    assert idx and all(d["sceneItemIndex"] == 2 for d in idx)   # directly above Feed A (1)
    tr = [d for k, d in s.sent if k == "SetSceneItemTransform"]
    assert tr and "sourceWidth" not in tr[0]["sceneItemTransform"]  # read-only keys dropped


def t_ensure_cover_items_existing_is_ok_and_no_feed_is_skipped():
    s = _FakeSession({
        "GetInputList": {"inputs": [{"inputName": "Feed A"}, {"inputName": "Feed A Cover"}]},
        "GetSceneItemList": {"sceneItems": [
            {"sourceName": "Feed A", "sceneItemId": 3, "sceneItemIndex": 1},
            {"sourceName": "Feed A Cover", "sceneItemId": 4, "sceneItemIndex": 2}]}})
    report, _ = m.ensure_cover_items("Feed A", "Feed A Cover", "/tmp/a.jpg",
                                     scenes=("Stint",), session=s)
    assert report == {"Stint": "ok"}
    assert not [k for k, _ in s.sent if k.startswith("Create")]


def t_get_scene_item_enabled_and_set_input_file():
    s = _FakeSession({"GetSceneItemId": {"sceneItemId": 5},
                      "GetSceneItemEnabled": {"sceneItemEnabled": False}})
    assert m.get_scene_item_enabled("Stint", "Feed B", session=s) == (False, "")
    assert m.set_input_file("Feed A Cover", "/tmp/a.jpg", session=s) == (True, "")
    assert ("SetInputSettings", {"inputName": "Feed A Cover",
                                 "inputSettings": {"file": "/tmp/a.jpg"}}) in s.sent


def t_cover_helpers_are_routed():
    for fn in ("ensure_cover_items", "get_scene_item_enabled", "set_input_file"):
        assert m.route_kind(fn) == "ctrl", fn
```

- [ ] **Step 2: Run them and check that they fail.** `python tests/test_obsws.py` → `AttributeError`.
- [ ] **Step 3: Implement** in `src/scripts/obs_ws.py`:

```python
COVER_SOURCES = {"A": "Feed A Cover", "B": "Feed B Cover"}   # spec 2026-10-17 Part C
COVER_SCENES = (STINT_SCENE, SPLIT_SCENE)
# sceneItemTransform keys OBS reports but rejects or ignores on Set.
_TRANSFORM_READ_ONLY = ("sourceWidth", "sourceHeight", "width", "height")


def _session_call(session, host, port, password, timeout, fn):
    """Run fn(session) on the given or a fresh session. (value, note), never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        return fn(session), ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def get_scene_item_enabled(scene, source, host="127.0.0.1", port=None,
                           password=None, timeout=2.0, session=None):
    def run(s):
        sid = s.request("GetSceneItemId", {"sceneName": scene, "sourceName": source}).get("sceneItemId")
        if sid is None:
            raise ValueError(f"scene item '{source}' not found in scene '{scene}'")
        return bool(s.request("GetSceneItemEnabled",
                              {"sceneName": scene, "sceneItemId": sid}).get("sceneItemEnabled"))
    return _session_call(session, host, port, password, timeout, run)


def set_input_file(input_name, path, host="127.0.0.1", port=None,
                   password=None, timeout=2.0, session=None):
    """Point an image input at `path`; re-sending the same path makes OBS reload it."""
    def run(s):
        s.request("SetInputSettings", {"inputName": input_name, "inputSettings": {"file": path}})
        return True
    ok, note = _session_call(session, host, port, password, timeout, run)
    return bool(ok), note


def ensure_cover_items(feed_source, cover_source, cover_file, scenes=COVER_SCENES,
                       host="127.0.0.1", port=None, password=None, timeout=3.0, session=None):
    """Create the cover input and its scene items directly above the feed item, with
    the feed item's transform, in every scene that has the feed item. Idempotent:
    an existing cover item is left alone. Returns ({scene: result}, note)."""
    def run(s):
        inputs = {i.get("inputName") for i in s.request("GetInputList", {}).get("inputs", [])}
        have_input = cover_source in inputs
        report = {}
        for scene in scenes:
            try:
                items = s.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
                feed = next((i for i in items if i.get("sourceName") == feed_source), None)
                if feed is None:
                    report[scene] = "no-feed"
                    continue
                if any(i.get("sourceName") == cover_source for i in items):
                    report[scene] = "ok"
                    continue
                if not have_input:
                    sid = s.request("CreateInput", {
                        "sceneName": scene, "inputName": cover_source, "inputKind": "image_source",
                        "inputSettings": {"file": cover_file}, "sceneItemEnabled": False,
                    }).get("sceneItemId")
                    have_input = True
                else:
                    sid = s.request("CreateSceneItem", {
                        "sceneName": scene, "sourceName": cover_source,
                        "sceneItemEnabled": False}).get("sceneItemId")
                s.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": sid,
                                                "sceneItemIndex": feed["sceneItemIndex"] + 1})
                tr = s.request("GetSceneItemTransform", {"sceneName": scene,
                               "sceneItemId": feed["sceneItemId"]}).get("sceneItemTransform") or {}
                tr = {k: v for k, v in tr.items() if k not in _TRANSFORM_READ_ONLY}
                if tr:
                    s.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid,
                                                        "sceneItemTransform": tr})
                report[scene] = "created"
            except Exception as exc:                  # noqa: BLE001  one scene, not all
                report[scene] = f"error: {str(exc) or exc.__class__.__name__}"
        return report
    report, note = _session_call(session, host, port, password, timeout, run)
    return report or {}, note
```

Add `"ensure_cover_items", "get_scene_item_enabled", "set_input_file"` to `_ROUTED_FNS`.

Check the `GetSceneItemList` index semantics: obs-websocket v5 `sceneItemIndex` 0 is the **bottom**, so `feed + 1` is directly above. If the real OBS check in Task 9 shows otherwise, flip it there.

- [ ] **Step 4: Run them and check that they pass.** `python tests/test_obsws.py`
- [ ] **Step 5: obs-sim.** In `tools/obs-sim.py` `_serve_one`, answer:
  - `GetInputMute` → `{"inputMuted": False}`;
  - `GetSceneItemId` → `{"sceneItemId": 1}`;
  - `GetSceneItemEnabled` → `{"sceneItemEnabled": True}`;
  - `GetInputList` → `{"inputs": []}`;
  - `GetSceneItemList` → `{"sceneItems": []}`.

  The last two make `ensure_cover_items` report `no-feed` in synthetic e2e runs instead of raising.
- [ ] **Step 6: Commit.** `feat(obs): helpers to create and drive per-feed cover images`

### Task 5: FeedCoverTap (keyframe stills from the ring)

**Files:**
- Modify: `src/relay/racecast-feeds.py` (after `_PreviewRingTap`, ~line 5190)
- Test: `tests/test_signal_hold.py`

**Interfaces:**
- Produces:
  - `COVER_WIDTH = 1920`;
  - `cover_ffmpeg_cmd(width=COVER_WIDTH) -> list[str]`;
  - `class FeedCoverTap(ring, path, log, spawn=None)`, with `.start()`, `.stop()` and `.last_write_ts` (monotonic or None).

- [ ] **Step 1: Failing tests:**

```python
import tempfile, threading, time

def t_cover_ffmpeg_cmd_decodes_keyframes_only():
    cmd = m.cover_ffmpeg_cmd(1920)
    assert cmd[cmd.index("-skip_frame") + 1] == "nokey"
    assert cmd.index("-skip_frame") < cmd.index("-i")          # decoder option, before input
    assert "scale=1920:-2" in cmd and cmd[-1] == "pipe:1" and "mjpeg" in cmd


class _Pipe:
    def __init__(self, chunks): self._c = list(chunks)
    def read(self, _n): return self._c.pop(0) if self._c else b""


def t_cover_tap_writes_the_latest_frame_atomically():
    jpeg1 = b"\xff\xd8one\xff\xd9"; jpeg2 = b"\xff\xd8two\xff\xd9"
    d = tempfile.mkdtemp(); path = os.path.join(d, "covers", "feed-a.jpg")
    class Ring:
        closed = False
        def live_offset(self): return 0
        def read(self, cursor, timeout, high=None): time.sleep(0.01); return b"", cursor
        def head(self): return b""
    def spawn(_tap):
        class P:
            def poll(self): return None
            def kill(self): pass
        class In:
            def write(self, _b): pass
            def flush(self): pass
            def close(self): pass
        return P(), In(), _Pipe([jpeg1 + jpeg2[:4], jpeg2[4:]])
    tap = m.FeedCoverTap(Ring(), path, LOG, spawn=spawn)
    tap.start()
    for _ in range(100):
        if tap.last_write_ts and open(path, "rb").read() == jpeg2:
            break
        time.sleep(0.02)
    tap.stop()
    assert open(path, "rb").read() == jpeg2
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
```

Check `ring_join(self.ring)` (used by `_PreviewRingTap._feed_stdin`) against the fake ring. If it needs more than `head()`, extend the fake `Ring` to match `ring_join`'s reads before running.

- [ ] **Step 2: Run them and check that they fail** (`AttributeError: cover_ffmpeg_cmd`).
- [ ] **Step 3: Implement:**

```python
COVER_WIDTH = 1920   # full-frame cover still (spec 2026-10-17 Part C)


def cover_ffmpeg_cmd(width=COVER_WIDTH):
    """Argv: ring bytes on stdin -> one full-width JPEG per KEYFRAME on stdout. Decoding
    keyframes only keeps an always-on tap cheap (~one frame every 2 s on YouTube)."""
    return ["ffmpeg", "-nostdin", "-loglevel", "error", "-skip_frame", "nokey",
            "-i", "pipe:0", "-map", "0:v:0", "-fps_mode", "passthrough",
            "-vf", f"scale={width}:-2", "-q:v", "3",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]


class FeedCoverTap:
    """Keeps `path` holding a current still of one feed: ring bytes -> ffmpeg (keyframes
    only) -> each JPEG written atomically (temp + os.replace), so OBS never reads a
    half-written file. Best effort: any spawn/read failure just stops writing; the
    feed is never touched. `spawn(tap) -> (proc, stdin, stdout)` is injectable."""

    def __init__(self, ring, path, log, spawn=None):
        self.ring, self.path, self.log = ring, path, log
        self._spawn = spawn or self._spawn_real
        self._proc = None
        self._stop = threading.Event()
        self.last_write_ts = None

    def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _spawn_real(self, _tap):
        ff = subprocess.Popen(cover_ffmpeg_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=external_tool_env(),
                              **_no_window_kwargs())
        return ff, ff.stdin, ff.stdout

    def _run(self):
        try:
            self._proc, stdin, video = self._spawn(self)
        except Exception as e:                       # noqa: BLE001 best-effort
            self.log.info("cover tap %s spawn error: %s", self.path, e)
            return
        threading.Thread(target=self._feed_stdin, args=(stdin,), daemon=True).start()
        buf = b""
        while not self._stop.is_set():
            chunk = video.read(65536)
            if not chunk:
                break
            frames, buf = split_mjpeg_frames(buf + chunk)
            if frames:
                self._write(frames[-1])
        self._kill()

    def _write(self, jpeg):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(jpeg)
            os.replace(tmp, self.path)
            self.last_write_ts = time.monotonic()
        except OSError as e:
            self.log.debug("cover write %s failed: %s", self.path, e)

    _feed_stdin = _PreviewRingTap._feed_stdin     # same ring pump: live edge + fMP4 init

    def _kill(self):
        p = self._proc
        try:
            if p and p.poll() is None:
                p.kill()
        except Exception:                            # noqa: BLE001
            pass

    def stop(self):
        self._stop.set()
        self._kill()
```

`_feed_stdin` reads `self.ring` and `self._stop`, which `FeedCoverTap` has, so reusing the function is safe. If lint flags the class-attribute reuse, copy the 20-line method instead.

- [ ] **Step 4: Run them and check that they pass.** `python tests/test_signal_hold.py`
- [ ] **Step 5: Commit.** `feat(relay): keep a current keyframe still of every fan-out feed`

### Task 6: Consumer flow tracking and the cover rule

**Files:**
- Modify: `src/relay/racecast-feeds.py`:
  - `FeedFanoutServer._serve`, around lines 5517-5530;
  - new method `consumer_flowing_s`;
  - a new pure function near `auto_cover_action`, around line 688.
- Test: `tests/test_signal_hold.py`

**Interfaces:**
- Produces:
  - `COVER_READY_S = 1.5`, `COVER_FLOW_GAP_S = 1.0`;
  - `FeedFanoutServer.consumer_flowing_s(now) -> float | None`;
  - `cover_visible(*, enabled, serving, consumer_flowing_s, hold_active, feed_item_shown) -> bool`.

- [ ] **Step 1: Failing tests:**

```python
def t_cover_visible_table():
    base = dict(enabled=True, serving=True, consumer_flowing_s=2.0,
                hold_active=False, feed_item_shown=True)
    assert m.cover_visible(**base) is False                              # playing: down
    assert m.cover_visible(**{**base, "consumer_flowing_s": 1.0}) is True  # not yet ready
    assert m.cover_visible(**{**base, "consumer_flowing_s": None}) is True # no consumer
    assert m.cover_visible(**{**base, "serving": False}) is True
    assert m.cover_visible(**{**base, "hold_active": True}) is True


def t_cover_hidden_when_feed_item_hidden():
    assert m.cover_visible(enabled=True, serving=False, consumer_flowing_s=None,
                           hold_active=True, feed_item_shown=False) is False


def t_cover_disabled_is_never_shown():
    assert m.cover_visible(enabled=False, serving=False, consumer_flowing_s=None,
                           hold_active=True, feed_item_shown=True) is False


def t_consumer_flowing_s_tracks_first_and_last_send():
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring=None, log=LOG)
    assert srv.consumer_flowing_s(100.0) is None
    srv._consumers[1] = {"first_send_ts": 98.0, "last_send_ts": 99.8}
    assert srv.consumer_flowing_s(100.0) == 2.0
    srv._consumers[1]["last_send_ts"] = 98.5                  # stopped sending 1.5 s ago
    assert srv.consumer_flowing_s(100.0) is None
```

- [ ] **Step 2: Run them and check that they fail.**
- [ ] **Step 3: Implement.**

The pure rule, next to `auto_cover_action`:

```python
COVER_READY_S = 1.5       # OBS must have received bytes this long before a cover lowers
COVER_FLOW_GAP_S = 1.0    # a consumer silent longer than this is not "flowing"


def cover_visible(*, enabled, serving, consumer_flowing_s, hold_active, feed_item_shown):
    """Whether a feed's cover image should be shown (spec 2026-10-17 Part C). Shown
    unless OBS is demonstrably playing the feed; never over a hidden feed item. Pure."""
    if not enabled or not feed_item_shown:
        return False
    if hold_active:
        return True
    return not (serving and consumer_flowing_s is not None
                and consumer_flowing_s >= COVER_READY_S)
```

In `FeedFanoutServer._serve`, after `conn.sendall(data)`, inside the existing `with self._consumers_lock:` block:

```python
                        st["cycle_ts"] = time.monotonic()  # send completed
                        st["last_send_ts"] = st["cycle_ts"]
                        st.setdefault("first_send_ts", st["cycle_ts"])
```

New method on `FeedFanoutServer`:

```python
    def consumer_flowing_s(self, now):
        """Seconds the longest-flowing consumer has been receiving bytes without a gap
        over COVER_FLOW_GAP_S, or None. A send gap restarts the count, so a consumer
        that resumes after a stall must flow COVER_READY_S again. `now` is monotonic."""
        best = None
        with self._consumers_lock:
            for st in self._consumers.values():
                first, last = st.get("first_send_ts"), st.get("last_send_ts")
                if first is None or last is None:
                    continue
                if now - last > COVER_FLOW_GAP_S:
                    st.pop("first_send_ts", None)       # gap: restart the count
                    continue
                best = max(best or 0.0, now - first)
        return best
```

The test's second case expects `None` after the gap. The pop also makes the next send restart `first_send_ts` through `setdefault`.

- [ ] **Step 4: Run them and check that they pass.** `python tests/test_signal_hold.py && python tests/test_fanout.py`
- [ ] **Step 5: Commit.** `feat(relay): know when OBS is really playing a feed`

### Task 7: Relay cover loop (create, refresh, show/hide)

**Files:**
- Modify: `src/relay/racecast-feeds.py`:
  - `Relay.__init__`, near `self._rebuild_guard`, ~line 8563;
  - `Relay.start`, ~line 8610;
  - new methods after `_maybe_auto_cover`, ~line 9380;
  - `Relay.status`;
  - the feed-state reflect sites at ~4647 and ~4728 (pre-show the incoming cover).
- Test: `tests/test_signal_hold.py`

**Interfaces:**
- Consumes: `cover_visible`, `FeedCoverTap`, `FeedFanoutServer.consumer_flowing_s`, `obs_ws.ensure_cover_items`, `obs_ws.get_scene_item_enabled`, `obs_ws.set_input_file`, `obs_ws.COVER_SOURCES`, `obs_ws.COVER_SCENES`, `obs_ws.FEED_SOURCES`.
- Produces:
  - `covers_enabled(environ) -> bool` (env `RACECAST_FEED_COVERS`, default on);
  - `COVER_TICK_S = 1.0`, `COVER_REFRESH_S = 5.0`, `COVER_REASSERT_S = 30.0`;
  - `Relay._cover`: `{feed: {"shown": bool|None, "applied": float, "refreshed": float, "status": "shown"|"hidden"|"missing"|"off"}}`;
  - `Relay._hold_active(feed) -> bool` (returns False until Task 10 defines holds);
  - `Relay._cover_tick(now)` and `Relay._pre_show_cover(feed)`;
  - `/status` `feeds.<X>.cover`.

- [ ] **Step 1: Failing tests** with a fake obs facade and fake feeds:

```python
class FakeObs:
    def __init__(self, missing=False, feed_shown=True):
        self.calls, self.missing, self.feed_shown = [], missing, feed_shown
    def ensure_cover_items(self, feed, cover, path, scenes=None):
        self.calls.append(("ensure", cover))
        return ({s: "no-feed" if self.missing else "ok" for s in ("Stint", "Splitscreen")}, "")
    def get_scene_item_enabled(self, scene, source):
        return (self.feed_shown, "")
    def set_scene_item_enabled(self, scene, source, enabled):
        self.calls.append(("enable", scene, source, enabled)); return (True, "")
    def set_input_file(self, name, path):
        self.calls.append(("file", name)); return (True, "")


def _relay_for_covers(obs, flowing=None, serving=True):
    r = m.Relay.__new__(m.Relay)            # no network, no threads
    r._obs, r.obs_note, r.fanout = obs, "", True
    r._covers = True
    r._cover = {}
    r._cover_dir = tempfile.mkdtemp()
    r._cover_ensured_ts = None
    class Srv:
        def consumer_flowing_s(self, now): return flowing
    class F:
        def __init__(s, name): s.name, s.paused, s.phase = name, False, "serving" if serving else "idle"
        fanout_server = Srv(); cover_tap = None
    r.feeds = {"A": F("A"), "B": F("B")}
    return r


def t_cover_tick_shows_until_flowing_then_hides():
    obs = FakeObs()
    r = _relay_for_covers(obs, flowing=0.5)
    r._cover_tick(100.0)
    assert ("enable", "Stint", "Feed A Cover", True) in obs.calls
    r.feeds["A"].fanout_server.consumer_flowing_s = lambda now: 2.0
    obs.calls.clear(); r._cover_tick(101.0)
    assert ("enable", "Stint", "Feed A Cover", False) in obs.calls


def t_cover_tick_applies_only_on_change_and_reasserts():
    obs = FakeObs(); r = _relay_for_covers(obs, flowing=None)
    r._cover_tick(100.0); obs.calls.clear()
    r._cover_tick(101.0)
    assert not [c for c in obs.calls if c[0] == "enable"]
    r._cover_tick(100.0 + m.COVER_REASSERT_S + 1)
    assert [c for c in obs.calls if c[0] == "enable"]


def t_cover_tick_missing_items_never_raise():
    obs = FakeObs(missing=True); r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    assert r._cover["A"]["status"] == "missing"
    assert not [c for c in obs.calls if c[0] == "enable"]


def t_covers_enabled_env():
    assert m.covers_enabled({}) is True
    assert m.covers_enabled({"RACECAST_FEED_COVERS": "0"}) is False
```

Construct `Relay` via `__new__` and set only the attributes the tick reads. If `_cover_tick` needs more, add them to `_relay_for_covers`. Do not boot a real Relay.

- [ ] **Step 2: Run them and check that they fail.**
- [ ] **Step 3: Implement.**

Module level:

```python
COVER_TICK_S = 1.0
COVER_REFRESH_S = 5.0      # re-point the image input at most this often while shown
COVER_REASSERT_S = 30.0    # re-send an unchanged state to heal a lost request


def covers_enabled(environ):
    return (environ.get("RACECAST_FEED_COVERS") or "").strip().lower() not in _FANOUT_FALSEY
```

In `Relay.__init__`:

```python
        self._covers = covers_enabled(os.environ) and self.fanout and not self.solo
        self._cover = {}                  # feed -> {"shown", "applied", "refreshed", "status"}
        self._cover_dir = os.path.join(<the relay's per-profile runtime dir>, "covers")
        self._cover_ensured_ts = None
```

Find the per-profile runtime dir attribute the relay already uses for its logs or `runtime/<profile>`: `grep -n "runtime_dir\|self.state_dir\|profile_runtime" src/relay/racecast-feeds.py | head`. Use that attribute. Never hardcode a path.

In `Relay.start`, after each fan-out server is created (`f.fanout_server = srv`):

```python
                if self._covers:
                    f.cover_tap = FeedCoverTap(f.ring, self._cover_path(k), LOG).start()
```

Also set `self.cover_tap = None` in `Feed.__init__`, and start the loop next to `_auto_cover_loop`:

```python
        if self._covers:
            threading.Thread(target=self._cover_loop, daemon=True).start()
```

Methods:

```python
    def _cover_path(self, feed):
        return os.path.join(self._cover_dir, f"feed-{feed.lower()}.jpg")

    def _hold_active(self, feed):
        """True while a signal hold covers this feed (Task 10 fills this in)."""
        return False

    def _cover_loop(self):
        while not self._hb_stop.is_set():
            try:
                self._cover_tick(time.monotonic())
            except Exception as exc:  # noqa: BLE001  best-effort; never break the loop
                LOG.debug("cover tick error (ignored): %s", exc, exc_info=True)
            self._hb_stop.wait(COVER_TICK_S)

    def _ensure_covers(self, now):
        """Create missing cover items (startup and every COVER_REASSERT_S while missing)."""
        if self._cover_ensured_ts is not None and now - self._cover_ensured_ts < COVER_REASSERT_S:
            return
        self._cover_ensured_ts = now
        for k in self.feeds:
            report, note = self._obs.ensure_cover_items(
                _obs_ws.FEED_SOURCES[k], _obs_ws.COVER_SOURCES[k], self._cover_path(k),
                scenes=_obs_ws.COVER_SCENES)
            ok = any(v in ("ok", "created") for v in report.values())
            st = self._cover.setdefault(k, {"shown": None, "applied": None, "refreshed": None})
            if not ok:
                st["status"] = "missing"
            elif st.get("status") in (None, "missing"):
                st["status"] = "hidden"       # found or created; the tick decides shown/hidden
            if note:
                self.obs_note = note

    def _cover_tick(self, now):
        if not self._covers or _obs_ws is None:
            return
        self._ensure_covers(now)
        for k, f in self.feeds.items():
            st = self._cover.setdefault(k, {"shown": None, "applied": None,
                                            "refreshed": None, "status": "missing"})
            if st["status"] == "missing":
                continue
            srv = f.fanout_server
            flowing = srv.consumer_flowing_s(now) if srv is not None else None
            serving = not f.paused and f.phase == "serving"
            for scene in _obs_ws.COVER_SCENES:
                shown, _ = self._obs.get_scene_item_enabled(scene, _obs_ws.FEED_SOURCES[k])
                want = cover_visible(enabled=True, serving=serving, consumer_flowing_s=flowing,
                                     hold_active=self._hold_active(k),
                                     feed_item_shown=bool(shown))
                self._apply_cover(k, scene, want, now, st)

    def _apply_cover(self, feed, scene, want, now, st):
        stale = st["applied"] is None or now - st["applied"] >= COVER_REASSERT_S
        if st.get("last_" + scene) == want and not stale:
            return
        if want and (st["refreshed"] is None or now - st["refreshed"] >= COVER_REFRESH_S):
            self._obs.set_input_file(_obs_ws.COVER_SOURCES[feed], self._cover_path(feed))
            st["refreshed"] = now
        ok, note = self._obs.set_scene_item_enabled(scene, _obs_ws.COVER_SOURCES[feed], want)
        if not ok:
            self.obs_note = note or self.obs_note
            return
        st["last_" + scene], st["applied"] = want, now
        st["status"] = "shown" if any(st.get("last_" + s) for s in _obs_ws.COVER_SCENES) else "hidden"

    def _pre_show_cover(self, feed):
        """Before a cut makes `feed` visible in Stint, raise its cover there so OBS's
        first black frames are never seen. Best effort; the tick lowers it later."""
        if not self._covers or _obs_ws is None:
            return
        if self._cover.get(feed, {}).get("status") in (None, "missing"):
            return
        self._obs.set_input_file(_obs_ws.COVER_SOURCES[feed], self._cover_path(feed))
        self._obs.set_scene_item_enabled(_obs_ws.STINT_SCENE, _obs_ws.COVER_SOURCES[feed], True)
```

**Pre-show at the cut sites.** At the two reflect sites (~4647 split and ~4728 stint), call `relay._pre_show_cover(live)` **before** the intents are reflected, only when the cut makes a different feed visible. Read both call sites first and pass the incoming feed.

In `Relay.status` per feed: `"cover": self._cover.get(k, {}).get("status", "off" if not self._covers else "missing"),`

- [ ] **Step 4: Run them and check that they pass.** `python tests/test_signal_hold.py && python tests/test_pov.py && python tests/test_fanout.py`
- [ ] **Step 5: Commit.** `feat(relay): cover a feed with its own still while OBS (re)opens it`

### Task 8: Docs and Panel note for covers

**Files:**
- Modify: `src/relay/CLAUDE.md` (a "Feed covers" paragraph after the fan-out paragraph: sources, rule, env switch, the never-in-intents constraint)
- Modify: `src/docs/wiki/Troubleshooting.md` (black on Splitscreen: covers; `RACECAST_FEED_COVERS=0` to disable)
- Modify: `.env.example` (document `RACECAST_FEED_COVERS`). `.env.example` lives under `src/`; find it with `git ls-files | grep env.example`.

- [ ] **Step 1:** Write the three doc edits. Name the mechanism and the switch only, no crew procedure.
- [ ] **Step 2:** `python tools/build.py`. It must exit 0.
- [ ] **Step 3: Commit.** `docs(relay): document feed covers`

### Task 9: Real-OBS check (manual, with the producer, not live), then PR 2

- [ ] **Step 1:** With the producer, outside any event:
  1. start the relay on a test profile with two test streams (memory: use smoketest discovery for test feeds);
  2. confirm `Feed A Cover` and `Feed B Cover` were created above the feed items in Stint and Splitscreen;
  3. do a Splitscreen handover: no black;
  4. press RESET: no black;
  5. confirm the cover lowers within ~2 s.
- [ ] **Step 2:** If OBS does not reload the image on `SetInputSettings` with an unchanged path (spec risk 3), alternate between `feed-a-0.jpg` and `feed-a-1.jpg` in `FeedCoverTap._write` and `_cover_path`, with a test. Re-run the check.
- [ ] **Step 3:** If the cover index lands below the feed, change `+ 1` in `ensure_cover_items` accordingly, with a test.
- [ ] **Step 4:** Run the gates and open the PR.
  - Title: `feat(relay): cover a feed with its own still while OBS (re)opens it`.
  - Body: "Closes #919", the manual check results, the env switch.
  - Arm auto-merge.

---

# PR 3: Part B, signal hold (Closes #918)

Branch: `feat/feed-signal-hold` off fresh `origin/main` (after PR 2 merged).

### Task 10: Hold state machine and relay wiring

**Files:**
- Modify: `src/relay/racecast-feeds.py`:
  - pure functions near `cover_visible`;
  - `Feed.__init__` and the `_serve_fanout` byte pump, ~line 8097;
  - `Relay.__init__`, `_hold_active`, a new `_hold_tick`, `_cover_loop`, `_backlog_shed_tick`, `_freeze_tick`, the `/obs/feed-reset` handler at ~12347, and `status`.
- Test: `tests/test_signal_hold.py`

**Interfaces:**
- Consumes: `cover_visible` via `_hold_active`, `Relay._cover_tick`, `Relay._apply_cover`, `obs_ws.get_input_mute`, `obs_ws.set_input_mute`, `obs_ws.FEED_SOURCES`, `Relay._record_event(now, type, label, metadata)`, `Feed._obs_reconnect()`, `Relay._note_obs_splice(feed)`.
- Produces:
  - `SIGNAL_HOLD_ENGAGE_IDLE_S = 1.0`, `SIGNAL_HOLD_RELEASE_STEADY_S = 5.0`, `RESERVE_EMPTY_S = 0.25`, `STEADY_GAP_S = 1.5`;
  - `signal_hold_enabled(environ) -> bool` (`RACECAST_FEED_SIGNAL_HOLD`, default on);
  - `steady_since_update(prev_since, last_ts, now, gap_s=STEADY_GAP_S) -> float`;
  - `signal_hold_step(state, *, reserve_s, last_byte_age_s, steady_s, cancel) -> (state, action)`, where state is `"live"|"hold"` and action is `None|"engage"|"release"|"cancel"`;
  - `Feed.steady_since`;
  - `Relay._hold`: `{feed: {"state", "since", "stint", "muted_by_us", "unmute_pending"}}`;
  - `Relay.signal_hold_notice() -> str | None` (Task 11 fills the text);
  - `Relay.cancel_signal_hold(feed, reason)`;
  - `/status` `feeds.<X>.signal_hold`.

- [ ] **Step 1: Failing tests:**

```python
S = m.signal_hold_step

def t_hold_short_gap_absorbed_by_reserve_never_engages():
    assert S("live", reserve_s=1.2, last_byte_age_s=1.6, steady_s=0.0, cancel=False) == ("live", None)


def t_hold_engages_when_reserve_empty_and_idle():
    assert S("live", reserve_s=0.1, last_byte_age_s=1.1, steady_s=0.0, cancel=False) == ("hold", "engage")
    assert S("live", reserve_s=0.1, last_byte_age_s=0.5, steady_s=0.0, cancel=False) == ("live", None)


def t_hold_single_burst_does_not_release():
    assert S("hold", reserve_s=2.0, last_byte_age_s=0.1, steady_s=1.0, cancel=False) == ("hold", None)


def t_hold_releases_after_steady_window():
    assert S("hold", reserve_s=3.0, last_byte_age_s=0.1, steady_s=5.0, cancel=False) == ("live", "release")


def t_hold_cancel_wins():
    assert S("hold", reserve_s=0.0, last_byte_age_s=9.0, steady_s=0.0, cancel=True) == ("live", "cancel")
    assert S("live", reserve_s=0.0, last_byte_age_s=9.0, steady_s=0.0, cancel=True) == ("live", None)


def t_hold_suzuka_2026_10_17_sequence():
    """21:07: gaps 4.03 -> 5.72 s, backlog floor 0.8 s: engages, no release mid-stall."""
    st, seen = "live", []
    for reserve, age, steady in [(3.9, 0.2, 30), (0.8, 0.4, 30), (0.1, 1.2, 0),
                                 (0.0, 4.0, 0), (1.9, 0.1, 0.5), (0.2, 1.5, 0),
                                 (2.5, 0.1, 2.0), (3.0, 0.1, 5.2)]:
        st, a = S(st, reserve_s=reserve, last_byte_age_s=age, steady_s=steady, cancel=False)
        seen.append(a)
    assert seen == [None, None, "engage", None, None, None, None, "release"], seen


def t_steady_since_update():
    assert m.steady_since_update(None, None, 10.0) == 10.0
    assert m.steady_since_update(5.0, 9.5, 10.0) == 5.0       # 0.5 s gap: still steady
    assert m.steady_since_update(5.0, 8.0, 10.0) == 10.0      # 2.0 s gap: restart


def t_signal_hold_enabled_env():
    assert m.signal_hold_enabled({}) is True
    assert m.signal_hold_enabled({"RACECAST_FEED_SIGNAL_HOLD": "0"}) is False
```

Relay-level tests reuse `_relay_for_covers`. Extend `FakeObs` with:

```python
    def get_input_mute(self, name): return (self.director_muted, "")
    def set_input_mute(self, name, muted):
        self.calls.append(("mute", name, muted)); return (True, "")
```

and set `director_muted = False` in `FakeObs.__init__`. Then:

```python
def _hold_relay(director_muted=False):
    obs = FakeObs(); obs.director_muted = director_muted
    r = _relay_for_covers(obs, flowing=None)
    r._signal_hold = True; r._hold = {}; r._hb_rebuild = []
    r.live_feed = lambda: "A"
    f = r.feeds["A"]
    f.is_local, f.last_byte_ts, f.steady_since = False, 100.0, 100.0
    f.current_channel = lambda: ("https://youtube.com/live/x", 2)
    f._obs_reconnect = lambda: r._hb_rebuild.append("A")
    r._note_obs_splice = lambda feed: None
    r._record_event = lambda *a: obs.calls.append(("event", a[1]))
    r.feeds["A"].fanout_server.consumer_backlog = lambda now: 0.0
    r.feeds["A"].fanout_server.consumer_attached = lambda: True
    return r, obs


def t_hold_engage_mutes_and_records():
    r, obs = _hold_relay()
    r._hold_tick(101.5)                          # 1.5 s without bytes, reserve 0
    assert r._hold_active("A") and ("mute", "Feed A", True) in obs.calls
    assert ("event", "signal_hold") in obs.calls


def t_hold_release_rebuilds_and_unmutes_after_cover_lowers():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]; f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)
    assert r._hb_rebuild == ["A"] and not r._hold_active("A")
    assert ("mute", "Feed A", False) not in obs.calls     # still under the cover
    r.feeds["A"].fanout_server.consumer_flowing_s = lambda now: 2.0
    r._hold_tick(109.0)
    assert ("mute", "Feed A", False) in obs.calls


def t_hold_release_keeps_director_mute():
    r, obs = _hold_relay(director_muted=True)
    r._hold_tick(101.5)
    assert ("mute", "Feed A", True) not in obs.calls      # already muted: not ours
    f = r.feeds["A"]; f.last_byte_ts, f.steady_since = 107.0, 101.9
    r.feeds["A"].fanout_server.consumer_flowing_s = lambda now: 2.0
    r._hold_tick(107.1); r._hold_tick(109.0)
    assert ("mute", "Feed A", False) not in obs.calls


def t_hold_cancel_restores_only_our_mute():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    r.cancel_signal_hold("A", "reset")
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls
    assert r._hb_rebuild == []


def t_hold_never_for_local_or_paused_or_off_air():
    r, obs = _hold_relay()
    r.feeds["A"].is_local = True
    r._hold_tick(101.5)
    assert not r._hold_active("A")
```

`Feed.is_local` is used here. Check how the relay tells a local stint today (`grep -n "is_local\|def _is_local\|local_feeds" src/relay/racecast-feeds.py | head`) and use that predicate in `_hold_tick` instead. Make the test set the same attribute or monkeypatch it.

- [ ] **Step 2: Run them and check that they fail.**
- [ ] **Step 3: Implement.**

Pure functions:

```python
SIGNAL_HOLD_ENGAGE_IDLE_S = 1.0
SIGNAL_HOLD_RELEASE_STEADY_S = 5.0
RESERVE_EMPTY_S = 0.25
STEADY_GAP_S = 1.5


def signal_hold_enabled(environ):
    return (environ.get("RACECAST_FEED_SIGNAL_HOLD") or "").strip().lower() not in _FANOUT_FALSEY


def steady_since_update(prev_since, last_ts, now, gap_s=STEADY_GAP_S):
    """Start of the current steady inbound run: restarts on a gap over gap_s. Pure."""
    if prev_since is None or last_ts is None or now - last_ts > gap_s:
        return now
    return prev_since


def signal_hold_step(state, *, reserve_s, last_byte_age_s, steady_s, cancel,
                     engage_idle_s=SIGNAL_HOLD_ENGAGE_IDLE_S,
                     release_steady_s=SIGNAL_HOLD_RELEASE_STEADY_S):
    """LIVE <-> HOLD for one on-air feed (spec 2026-10-17 Part B). Pure."""
    if cancel:
        return ("live", "cancel") if state == "hold" else ("live", None)
    if state == "live":
        if (reserve_s is not None and reserve_s <= RESERVE_EMPTY_S
                and last_byte_age_s is not None and last_byte_age_s >= engage_idle_s):
            return "hold", "engage"
        return "live", None
    if steady_s is not None and steady_s >= release_steady_s and (last_byte_age_s or 0) < STEADY_GAP_S:
        return "live", "release"
    return "hold", None
```

In `Feed.__init__`: `self.steady_since = None`. In the `_serve_fanout` pump, before `self.last_byte_ts = _now`:

```python
                self.steady_since = steady_since_update(self.steady_since, self.last_byte_ts, _now)
```

Where the serve resets `last_byte_ts = None` (~line 8064), also reset `self.steady_since = None`.

In `Relay.__init__`:

```python
        self._signal_hold = signal_hold_enabled(os.environ) and self.fanout and not self.solo
        self._hold = {}
```

Replace the Task 7 stub:

```python
    def _hold_active(self, feed):
        return self._hold.get(feed, {}).get("state") == "hold"
```

The hold tick runs in `_cover_loop`, before `_cover_tick`:

```python
                self._hold_tick(time.monotonic())
                self._cover_tick(time.monotonic())
```

```python
    def _hold_tick(self, now):
        """One signal-hold step for the on-air feed, plus the deferred unmute of a just
        released one (it waits until its cover has lowered)."""
        for k, st in list(self._hold.items()):
            if st.get("unmute_pending"):
                srv = self.feeds[k].fanout_server
                flowing = srv.consumer_flowing_s(now) if srv is not None else None
                if flowing is not None and flowing >= COVER_READY_S:
                    self._obs.set_input_mute(_obs_ws.FEED_SOURCES[k], False)
                    st["unmute_pending"] = False
        if not self._signal_hold or _obs_ws is None:
            return
        live = self.live_feed()
        f = self.feeds.get(live)
        st = self._hold.setdefault(live, {"state": "live"})
        _ch, idx = f.current_channel()
        eligible = (not f.paused and f.phase == "serving" and not self._feed_is_local(f)
                    and f.fanout_server is not None and f.fanout_server.consumer_attached())
        cancel = (st["state"] == "hold" and (not eligible or st.get("stint") != idx))
        if st["state"] == "live" and not eligible:
            return
        reserve = f.fanout_server.consumer_backlog(now) if f.fanout_server else None
        age = None if f.last_byte_ts is None else now - f.last_byte_ts
        steady = None if f.steady_since is None else now - f.steady_since
        state, action = signal_hold_step(st["state"], reserve_s=reserve, last_byte_age_s=age,
                                         steady_s=steady, cancel=cancel)
        st["state"] = state
        if action == "engage":
            self._hold_engage(live, f, idx, now)
        elif action == "release":
            self._hold_release(live, f, now)
        elif action == "cancel":
            self._hold_end(live, now, "cancel")

    def _hold_engage(self, feed, f, idx, now):
        st = self._hold[feed]
        st.update(since=time.time(), stint=idx, unmute_pending=False)
        muted, _ = self._obs.get_input_mute(_obs_ws.FEED_SOURCES[feed])
        st["muted_by_us"] = muted is False
        if st["muted_by_us"]:
            self._obs.set_input_mute(_obs_ws.FEED_SOURCES[feed], True)
        self._cover.setdefault(feed, {})["refreshed"] = None   # force a fresh still now
        LOG.warning("signal hold on %s: source stalled past the reserve, covering", feed)
        self._record_event(time.time(), "signal_hold", f"Feed {feed} signal hold",
                           {"feed": feed, "stint": idx + 1})

    def _hold_release(self, feed, f, now):
        f._obs_reconnect()
        self._note_obs_splice(feed)
        with self._rebuild_lock:
            self._last_freeze_ts = now           # claim the shared rebuild cooldown
        self._hold_end(feed, now, "release", unmute_later=True)

    def _hold_end(self, feed, now, how, unmute_later=False):
        st = self._hold.get(feed, {})
        st["state"] = "live"
        if st.get("muted_by_us"):
            if unmute_later:
                st["unmute_pending"] = True
            else:
                self._obs.set_input_mute(_obs_ws.FEED_SOURCES[feed], False)
        dur = round(time.time() - st["since"], 1) if st.get("since") else None
        LOG.info("signal hold on %s ended (%s, %s s)", feed, how, dur)
        self._record_event(time.time(), "signal_hold_end", f"Feed {feed} signal hold {how}",
                           {"feed": feed, "how": how, "duration_s": dur,
                            "stint": (st.get("stint") or 0) + 1})
        st["since"] = None

    def cancel_signal_hold(self, feed, reason):
        if self._hold_active(feed):
            self._hold[feed]["state"] = "live"
            self._hold_end(feed, time.monotonic(), reason)

    def _feed_is_local(self, f):
        """Use the relay's existing local-stint predicate (see the test note)."""
```

Implement `_feed_is_local` with the predicate the relay already uses. Do not invent a new attribute.

The `muted_by_us` rule: we only mute when OBS says the input is unmuted. When OBS is unreachable (`None`), we do not mute, and therefore we never unmute.

**Stand-downs:**
- At the top of `_backlog_shed_tick` and `_freeze_tick`, after `live = self.live_feed()`, add `if self._hold_active(live): return`.
- In the `/obs/feed-reset` handler (~12347), call `relay.cancel_signal_hold(feed, "reset")` before its rebuild.

**`/status`** per feed: `"signal_hold": {"active": self._hold_active(k), "since": self._hold.get(k, {}).get("since")},`

- [ ] **Step 4: Run them and check that they pass.** `python tests/test_signal_hold.py && python tests/test_pov.py && python tests/test_fanout.py`
- [ ] **Step 5: Commit.** `feat(relay): freeze a stalling on-air feed under its cover`

### Task 11: Sheet notice in the HUD race-control slot

**Files:**
- Modify: `src/relay/racecast-feeds.py`:
  - a parser near `parse_rc_note_presets`, ~line 2193;
  - `HudSource.refresh`, ~line 7022, plus an accessor;
  - `Relay.signal_hold_notice`;
  - the `/hud/data` handler, ~line 11404.
- Test: `tests/test_signal_hold.py`

**Interfaces:**
- Produces:
  - `SIGNAL_HOLD_HEADERS = ("signal hold",)`;
  - `parse_signal_hold_text(text) -> str | None`;
  - `HudSource.signal_hold_text() -> str | None`;
  - `Relay.signal_hold_notice() -> str | None`.

- [ ] **Step 1: Failing tests:**

```python
CONFIG = "Team,Number,Race Control,Signal Hold\nA,1,Driver Swaps,Signal is being restored\nB,2,,\n"

def t_parse_signal_hold_text():
    assert m.parse_signal_hold_text(CONFIG) == "Signal is being restored"
    assert m.parse_signal_hold_text("Team,Signal Hold\nA,\n") is None
    assert m.parse_signal_hold_text("Team\nA\n") is None
    assert m.parse_signal_hold_text("") is None


def t_hud_notice_only_while_on_air_feed_holds():
    r, _obs = _hold_relay()
    r.hud_source = type("H", (), {"signal_hold_text": lambda s: "Signal is being restored"})()
    assert r.signal_hold_notice() is None
    r._hold_tick(101.5)
    assert r.signal_hold_notice() == "Signal is being restored"


def t_hud_notice_absent_text_keeps_sheet_value():
    r, _obs = _hold_relay()
    r.hud_source = type("H", (), {"signal_hold_text": lambda s: None})()
    r._hold_tick(101.5)
    assert r.signal_hold_notice() is None      # handler then leaves raceControl alone
```

Check how the Relay reaches the `HudSource` today (`grep -n "hud_source" src/relay/racecast-feeds.py | head`). If the handler receives it as a closure argument and `Relay` has no attribute, give `Relay` a `hud_source` attribute that is set where both are wired, and keep the handler's own reference too.

- [ ] **Step 2: Run them and check that they fail.**
- [ ] **Step 3: Implement.**

```python
# Configuration-tab column holding the HUD notice shown during a feed signal hold
# (spec 2026-10-17). First non-blank value; none -> no notice (never a made-up default).
SIGNAL_HOLD_HEADERS = ("signal hold",)


def parse_signal_hold_text(text):
    presets = _parse_preset_column(text, SIGNAL_HOLD_HEADERS)
    return presets[0] if presets else None
```

In `HudSource.refresh`, inside the guarded try, add `signal_hold_text = parse_signal_hold_text(config_text)`. In the locked commit block, add `self._signal_hold_text = signal_hold_text`. Initialise `self._signal_hold_text = None` in `__init__` and add:

```python
    def signal_hold_text(self):
        with self.lock:
            return self._signal_hold_text
```

On the `Relay`:

```python
    def signal_hold_notice(self):
        """The HUD race-control override while the ON-AIR feed is held, else None."""
        if not self._hold_active(self.live_feed()) or self.hud_source is None:
            return None
        return self.hud_source.signal_hold_text()
```

In the `/hud/data` handler, after `data = hud_source.data()`:

```python
                    notice = relay.signal_hold_notice()
                    if notice:
                        data["raceControl"] = notice      # relay-side override, no Sheet write
```

- [ ] **Step 4: Run them and check that they pass.**
- [ ] **Step 5: Commit.** `feat(relay): show the Sheet's signal-hold notice in the HUD`

### Task 12: Report section and Panel pill

**Files:**
- Modify: `src/scripts/report_build.py`:
  - event collection, ~line 485;
  - the HTML section after "Feed auto-recoveries", ~line 856;
  - the text/markdown summary, if the report has one: `grep -n "recoveries" src/scripts/report_build.py`.
- Modify: `src/director/director-panel.html`:
  - pill markup next to `#stBehind`, line 668;
  - `relayPoll`.
- Test: `tests/test_report_build.py`, and the panel test used in Task 3
- Modify: `src/docs/wiki/images/director-panel.png`, the wiki Director page, `src/relay/CLAUDE.md`, `.env.example`

- [ ] **Step 1: Failing report test** (follow the existing recovery-test pattern in `tests/test_report_build.py`). Given events `signal_hold` (ts 100, feed A, stint 3) and `signal_hold_end` (ts 123, duration 23.0), the report dict has:

```python
report["signal_holds"] == [{"ts": 100, "feed": "A", "stint": 3, "streamer": "<name>", "duration_s": 23.0}]
```

The rendered HTML contains `<h2>Signal holds</h2>`.

- [ ] **Step 2: Implement.**
  - In the event loop, collect `signal_hold` starts and pair each with the next `signal_hold_end` of the same feed (`duration_s` from its metadata).
  - Render a table with Time, Feed, Stint, Commentator and Duration. The note reads: "The relay covered the on-air feed with its last good picture, muted, while the source stalled past the reserve."
- [ ] **Step 3: Panel pill.** Add markup next to `#stBehind`:

```html
<span class="st warn" id="stHold" hidden title="The on-air feed stalled past the reserve; it is covered with its last picture and muted until the source is steady">SIGNAL HOLD <b>—</b></span>
```

In `relayPoll`, after `behindPill(d);`:

```js
const held = !solo && d.feeds ? ["A", "B"].filter(k => d.feeds[k] && d.feeds[k].signal_hold && d.feeds[k].signal_hold.active) : [];
$("#stHold").hidden = !held.length;
if (held.length) $("#stHold").querySelector("b").textContent = held.join(" ");
```

- [ ] **Step 4:**
  - Render and look (ui-visual-verification);
  - refresh `director-panel.png` with the pill shown, using a status stub in the screenshot harness;
  - record the marker.
- [ ] **Step 5: Docs.**
  - `src/relay/CLAUDE.md`: the hold paragraph, covering the rule, the env switch, the stand-downs and the no-Sheet-write notice.
  - The wiki Director page: the SIGNAL HOLD pill, the `Signal Hold` Configuration column and what viewers see.
  - `.env.example`: `RACECAST_FEED_SIGNAL_HOLD`.
- [ ] **Step 6: Commit.** `feat(report): list signal holds; show the hold in the Director Panel`

### Task 13: Threshold check against 2026-10-17, real-OBS check, PR 3

- [ ] **Step 1: Replay.** Read the producer's health history for 2026-10-17 (`runtime/erf-wspc/health-history.db`, read-only copy into the scratchpad, **never** the live file during an event). Tabulate per 30 s interval: inbound gap, backlog floor, and when the incidents opened. Check that:
  - every stall the producer called unacceptable (20:03, 21:04-21:07) satisfies the engage rule;
  - 17:54, 18:10 and 18:37 would have produced at most short holds.

  Write the table into the PR body. Change a constant only with the producer's OK.
- [ ] **Step 2: Real OBS, not live, with the producer.** Force a hold by blocking the feed's network for 6 s (or pause a local test stream source). Check:
  - the still and the muted audio;
  - the `Signal Hold` text in the race-control slot;
  - the return without black after 5 s steady;
  - the director's own mute survives.
- [ ] **Step 3:** Run the gates and open the PR.
  - Title: `feat(relay): freeze a stalling on-air feed under its cover`.
  - Body: "Closes #918", the replay table and the manual check.
  - Arm auto-merge.

---

## Self-review notes (done while writing)

- **Spec coverage:**
  - Part A → Tasks 1-3.
  - Part C covers → Tasks 4-9: the creation, the still, the rule, the never-over-hidden-feed case, the switch, `/status` and the pre-show at cuts.
  - Part B → Tasks 10-13:
    - engage, release and cancel;
    - mute ownership;
    - the notice;
    - the stand-downs (shed, freeze, RESET);
    - the shared cooldown and the splice note;
    - the report and the pill;
    - the threshold replay.
  - Spec "Testing" → unit tests per task, obs-sim answers in Task 4 Step 5, manual OBS checks in Tasks 9 and 13. A dedicated e2e run asserting the cover/hold fields is not separate: Tasks 7 and 10 cover the logic with fakes, and obs-sim keeps the synthetic e2e green.
- **Twitch:** Part A probes the YouTube-resolved manifest only. The spec mentions Twitch, but the relay serves Twitch by token through streamlink and has no manifest URL at that point. Twitch keeps `target_duration_s: null`. Note this in PR 1.
- **Names used across tasks:**
  - `cover_visible`, `COVER_READY_S`, `consumer_flowing_s`;
  - `_cover_tick`, `_apply_cover`, `_pre_show_cover`, `_hold_active`, `_hold_tick`, `cancel_signal_hold`, `signal_hold_notice`;
  - `COVER_SOURCES`, `COVER_SCENES`, `FEED_SOURCES`.
