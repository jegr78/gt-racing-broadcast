#!/usr/bin/env python3
"""Pure, import-testable assertion core for the e2e harness (tools/e2e.py).

Stdlib only. Everything here is exercised by tests/test_e2e.py without spawning
a relay; the heavy end-to-end run lives in tools/e2e.py."""
import collections
import csv as _csv
import io as _io
import json as _json
import os
import socket
import sys
import urllib.error
import urllib.request

CheckResult = collections.namedtuple("CheckResult", "name status message")
# status in {"pass", "fail", "skip"}


def http_request(url, method="GET", headers=None, data=None, timeout=10):
    """GET/POST returning (status, body_bytes, headers_dict) without raising on
    4xx/5xx. urllib raises HTTPError there; this reads it as a normal response so
    auth-gating checks can assert 401/404."""
    req = urllib.request.Request(url, method=method, data=data,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 loopback
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers or {})


def classify_capability(available, name):
    """Optional capability gate: when *available* is False, return a skip
    CheckResult; when True, return None so the caller runs the real check."""
    if available:
        return None
    return CheckResult(name, "skip", f"{name} unavailable")


def run_checks(checks, ctx):
    """Run each check(ctx) -> CheckResult. A raised exception becomes a fail.
    Returns (results, exit_code); exit_code is 1 iff any result failed."""
    results = []
    for fn in checks:
        try:
            r = fn(ctx)
            if not isinstance(r, CheckResult):
                r = CheckResult(getattr(fn, "__name__", "check"), "fail",
                                f"check returned {type(r).__name__}, not CheckResult")
        except Exception as exc:  # noqa: BLE001  a crashing check is a failure
            r = CheckResult(getattr(fn, "__name__", "check"), "fail",
                            f"{type(exc).__name__}: {exc}")
        results.append(r)
    code = 1 if any(r.status == "fail" for r in results) else 0
    return results, code


def summarize(results):
    """One line per check plus a totals line."""
    lines, n = [], {"pass": 0, "fail": 0, "skip": 0}
    for r in results:
        n[r.status] = n.get(r.status, 0) + 1
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[r.status]
        lines.append(f"  [{mark}] {r.name}" + (f": {r.message}" if r.message else ""))
    lines.append(f"  {n['pass']} passed, {n['fail']} failed, {n['skip']} skipped")
    return "\n".join(lines)

SCHEDULE_HEADER = ("URL", "Streamer", "Stint")


def build_schedule_csv(rows):
    """A header-mode schedule CSV (columns URL,Streamer,Stint) the relay's
    ScheduleSource parses 1:1. *rows* is an iterable of (url, streamer, stint).
    URLs must be real YouTube/Twitch host URLs: is_channel() rejects
    localhost, LAN and file."""
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(SCHEDULE_HEADER)
    for url, streamer, stint in rows:
        w.writerow([url, streamer, stint])
    return buf.getvalue()


def free_port():
    """An OS-assigned free TCP port on the loopback. Binds :0, reads it back and
    closes, so there is a small race window the caller closes by handing the port
    to a child immediately."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# Driving the frozen binary instead of `python src/racecast.py` guards the bugs
# the src/ dev build hides: a file or import missing from the PyInstaller bundle,
# and frozen path resolution. The argv assembly is pure so it is unit-tested
# without building a binary.

def binary_name(osname=None):
    """The racecast executable's filename for *osname* (os.name): 'racecast.exe'
    on Windows ('nt'), otherwise 'racecast'."""
    osname = os.name if osname is None else osname
    return "racecast.exe" if osname == "nt" else "racecast"


def default_binary_path(root, osname=None):
    """Where tools/build-binary.py drops the racecast executable:
    <root>/dist/bin/<binary_name>."""
    return os.path.join(root, "dist", "bin", binary_name(osname))


def service_launcher(binary, python=None, script=None):
    """Argv prefix that invokes the racecast CLI: [binary] when *binary* is set,
    otherwise the src/ dev path [python, script]. Callers append the subcommand
    and its args. The subcommand surface is identical either way, so the same
    checks run against both. *python* defaults to the current interpreter."""
    if binary:
        return [binary]
    return [python or sys.executable, script]


Ctx = collections.namedtuple(
    "Ctx",
    "relay_url disabled_relay_url ui_url token streamer_key expect own_stint"
    " fanout_feed_port fanout_relay_url")
Ctx.__new__.__defaults__ = (None, None, None)  # own_stint, fanout_feed_port and
                                                # fanout_relay_url are optional


def _get_json(url, headers=None):
    st, body, _ = http_request(url, headers=headers)
    return st, (_json.loads(body or b"null"))


def first_roster_streamer(status, body_bytes):
    """Given a `/schedule/data` response in the (status, body_bytes) shape
    http_request() returns, return the first roster streamer name, or None when
    the status is not 200, the body is empty or unparseable, or no row carries a
    streamer name.

    The relay serves /schedule/data as {"rows": [{"name": ...}, ...]}. This is the
    pure decode step the real-league run uses to pick a streamer to mint a cockpit
    token for, without hardcoding a name."""
    if status != 200:
        return None
    try:
        data = _json.loads(body_bytes or b"null")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or "").strip()
        if name:
            return name
    return None


def check_status_ok(ctx):
    st, data = _get_json(ctx.relay_url + "/status")
    if st != 200:
        return CheckResult("status_ok", "fail", f"/status HTTP {st}")
    if data.get("schedule_len") != ctx.expect["schedule_len"]:
        return CheckResult("status_ok", "fail",
                           f"schedule_len={data.get('schedule_len')}")
    if data.get("live", {}).get("stint") != ctx.expect["live_stint"]:
        return CheckResult("status_ok", "fail", f"live={data.get('live')}")
    return CheckResult("status_ok", "pass", "")


def check_cockpit_requires_token(ctx):
    st, _, _ = http_request(ctx.relay_url + "/cockpit/data")
    if st != 401:
        return CheckResult("cockpit_requires_token", "fail",
                           f"expected 401, got {st}")
    return CheckResult("cockpit_requires_token", "pass", "")


def check_cockpit_accepts_token(ctx):
    url = f"{ctx.relay_url}/cockpit?t={ctx.token}"
    st, _, hdrs = http_request(url)
    if st != 200:
        return CheckResult("cockpit_accepts_token", "fail", f"HTTP {st}")
    if "rc_console=" not in (hdrs.get("Set-Cookie") or ""):
        return CheckResult("cockpit_accepts_token", "fail", "no rc_console cookie")
    return CheckResult("cockpit_accepts_token", "pass", "")


def check_cockpit_tally(ctx):
    # /cockpit/data serves the cockpit_tally() fields flat at the top level, not
    # nested under a "tally" key. Read whichever the server gives.
    st, data = _get_json(f"{ctx.relay_url}/cockpit/data?t={ctx.token}")
    if st != 200:
        return CheckResult("cockpit_tally", "fail", f"HTTP {st}")
    tally = data.get("tally") if isinstance(data.get("tally"), dict) else data
    if "on_air" not in tally or "scheduled" not in tally:
        return CheckResult("cockpit_tally", "fail", f"tally shape: {tally}")
    up = tally.get("up_next") or {}
    # Regression guard for #191: the stint label must not double-print "stint".
    label = str(up.get("stint", "")) if isinstance(up, dict) else ""
    if label.lower().count("stint") > 1:
        return CheckResult("cockpit_tally", "fail", f"double stint: {label!r}")
    return CheckResult("cockpit_tally", "pass", "")


def check_cockpit_404_when_disabled(ctx):
    st, _, _ = http_request(ctx.disabled_relay_url + "/cockpit/data")
    if st != 404:
        return CheckResult("cockpit_404_when_disabled", "fail",
                           f"expected 404, got {st}")
    return CheckResult("cockpit_404_when_disabled", "pass", "")


def check_cockpit_timer_renders(ctx):
    """#191 regression: /cockpit/timer must serve real timer fields so the page
    renders a clock, not the literal '—' placeholder. The page falls back to '—'
    only when the JSON is absent, not visible or carries no duration, so this
    asserts a 200 with visible=True and a numeric remaining_s, duration_s or end."""
    st, data = _get_json(f"{ctx.relay_url}/cockpit/timer?t={ctx.token}")
    if st != 200:
        return CheckResult("cockpit_timer_renders", "fail", f"HTTP {st}")
    if data.get("error"):
        return CheckResult("cockpit_timer_renders", "fail", f"error: {data['error']}")
    if not data.get("visible"):
        return CheckResult("cockpit_timer_renders", "fail",
                           f"not visible (page would show '—'): {data}")
    nums = [data.get("remaining_s"), data.get("duration_s"), data.get("end")]
    if not any(isinstance(v, (int, float)) for v in nums):
        return CheckResult("cockpit_timer_renders", "fail",
                           f"no renderable timer value: {data}")
    return CheckResult("cockpit_timer_renders", "pass", "")


def check_chat_round_trip(ctx):
    """POST /chat/send {user,text}, then GET /chat/data must echo the message back
    from the relay's in-memory ring buffer."""
    marker = "e2e-chat-ping"
    payload = _json.dumps({"user": "e2e", "text": marker}).encode()
    st, body, _ = http_request(ctx.relay_url + "/chat/send", method="POST",
                               headers={"Content-Type": "application/json"},
                               data=payload)
    if st != 200:
        return CheckResult("chat_round_trip", "fail", f"send HTTP {st}: {body[:120]!r}")
    sent = _json.loads(body or b"null") or {}
    if not sent.get("ok"):
        return CheckResult("chat_round_trip", "fail", f"send rejected: {sent}")
    st, data = _get_json(ctx.relay_url + "/chat/data")
    if st != 200:
        return CheckResult("chat_round_trip", "fail", f"data HTTP {st}")
    texts = [m.get("text") for m in (data.get("messages") or [])]
    if marker not in texts:
        return CheckResult("chat_round_trip", "fail",
                           f"sent message not in /chat/data ({len(texts)} msgs)")
    return CheckResult("chat_round_trip", "pass", "")


def check_submission_pending(ctx):
    """#193: an own-row POST /cockpit/submit lands as pending, never
    auto-published, and the director's tailnet-only /submissions lists it."""
    url = "https://www.youtube.com/watch?v=e2eee2eee2e"
    payload = _json.dumps({"url": url, "stint": ctx.own_stint}).encode()
    st, body, _ = http_request(
        f"{ctx.relay_url}/cockpit/submit?t={ctx.token}", method="POST",
        headers={"Content-Type": "application/json"}, data=payload)
    if st != 200:
        return CheckResult("submission_pending", "fail",
                           f"submit HTTP {st}: {body[:160]!r}")
    res = _json.loads(body or b"null") or {}
    if not res.get("ok") or not res.get("id"):
        return CheckResult("submission_pending", "fail", f"submit shape: {res}")
    sub_id = res["id"]
    st, data = _get_json(ctx.relay_url + "/submissions")
    if st != 200:
        return CheckResult("submission_pending", "fail", f"/submissions HTTP {st}")
    pending = data.get("pending") or []
    if not any(e.get("id") == sub_id for e in pending):
        return CheckResult("submission_pending", "fail",
                           f"submission {sub_id} not pending ({len(pending)} entries)")
    return CheckResult("submission_pending", "pass", "")


def check_event_title_round_trip(ctx):
    """#207: POST /event/title sets the free-text event title; it must then surface
    in both /status and /cockpit/data. The state is relay-local (event.json) with no
    external push, so this is safe in synthetic and real-league mode alike."""
    title = "E2E - Round 7 - Spa 24h"
    payload = _json.dumps({"title": title}).encode()
    st, body, _ = http_request(ctx.relay_url + "/event/title", method="POST",
                               headers={"Content-Type": "application/json"},
                               data=payload)
    if st != 200:
        return CheckResult("event_title_round_trip", "fail",
                           f"POST HTTP {st}: {body[:120]!r}")
    res = _json.loads(body or b"null") or {}
    if res.get("title") != title:
        return CheckResult("event_title_round_trip", "fail", f"echo: {res}")
    st, data = _get_json(ctx.relay_url + "/status")
    if st != 200 or data.get("event_title") != title:
        return CheckResult("event_title_round_trip", "fail",
                           f"/status event_title={data.get('event_title')!r}")
    st, cd = _get_json(f"{ctx.relay_url}/cockpit/data?t={ctx.token}")
    if st != 200 or cd.get("event_title") != title:
        return CheckResult("event_title_round_trip", "fail",
                           f"/cockpit/data event_title={cd.get('event_title')!r}")
    return CheckResult("event_title_round_trip", "pass", "")


def check_status_live(ctx):
    """Structural real-league /status health: 200, a non-empty schedule and a live
    block, without pinning exact counts. The real schedule_len and live_stint are
    unknown without the live Sheet, so check_status_ok's fixed assertions do not
    apply."""
    st, data = _get_json(ctx.relay_url + "/status")
    if st != 200:
        return CheckResult("status_live", "fail", f"/status HTTP {st}")
    n = data.get("schedule_len")
    if not isinstance(n, int) or n <= 0:
        return CheckResult("status_live", "fail", f"schedule_len={n!r} (expected > 0)")
    live = data.get("live")
    if not isinstance(live, dict) or "feed" not in live:
        return CheckResult("status_live", "fail", f"no live block: {live!r}")
    return CheckResult("status_live", "pass", f"schedule_len={n}, live={live.get('feed')}")


def check_cc_api_cockpit(ctx):
    """Control Center /api/console/status responds 200 with an ok flag and a
    links list."""
    if not ctx.ui_url:
        return CheckResult("cc_api_cockpit", "skip", "no ui_url")
    st, data = _get_json(ctx.ui_url + "/api/console/status")
    if st != 200:
        return CheckResult("cc_api_cockpit", "fail", f"HTTP {st}")
    if "ok" not in data or not isinstance(data.get("links"), list):
        return CheckResult("cc_api_cockpit", "fail", f"shape: {data}")
    return CheckResult("cc_api_cockpit", "pass", "")


def _load_set_env_key():
    """Import the real `_set_env_key` from src/racecast.py, the single-key
    profile.env writer the #191 fix lives in. racecast.py imports cleanly as a
    module: no hyphen in the name, no import-time side effects."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return importlib.import_module("racecast")


def check_enable_preserves_keys(_ctx=None):
    """#191 regression: provisioning CONSOLE_SECRET must not wipe other profile.env
    keys. The bug was a single-pair write through the full-set merge_env_text, which
    dropped any key not re-passed. This exercises the same seam the zero-config
    auto-provision uses, `racecast._set_env_key(path, key, value)`, against a temp
    profile.env carrying several keys.

    Self-contained: its own tempfile fixture, no relay, no repo profiles/ and no
    side effects, so it is safe to run anywhere."""
    import tempfile
    rc = _load_set_env_key()
    tmp = tempfile.mkdtemp(prefix="racecast-e2e-enable-")
    try:
        ppath = os.path.join(tmp, "profile.env")
        pre = {
            "NAME": "Test League",
            "SHEET_ID": "1AbC_dEfGhIjKlMnOpQrStUvWxYz",
            "SHEET_PUSH_URL": "https://script.google.com/macros/s/AKfyc/exec",
            "CUSTOM_KEY": "keep-me-please",
        }
        with open(ppath, "w", encoding="utf-8") as fh:
            fh.write("# league config\n")
            for k, v in pre.items():
                fh.write(f"{k}={v}\n")
        res = rc._set_env_key(ppath, "CONSOLE_SECRET", "deadbeef" * 8)
        if not res.get("ok"):
            return CheckResult("enable_preserves_keys", "fail",
                               f"_set_env_key failed: {res.get('error')}")
        with open(ppath, encoding="utf-8") as fh:
            after = rc.parse_env_text(fh.read())
        for k, v in pre.items():
            if after.get(k) != v:
                return CheckResult("enable_preserves_keys", "fail",
                                   f"key {k!r} clobbered: was {v!r}, now {after.get(k)!r}")
        if not after.get("CONSOLE_SECRET"):
            return CheckResult("enable_preserves_keys", "fail",
                               "CONSOLE_SECRET was not added")
        return CheckResult("enable_preserves_keys", "pass",
                           f"{len(pre)} keys preserved + CONSOLE_SECRET added")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _fanout_http_ok(response_bytes):
    """Parse a raw HTTP/1.0 response and return (ok, msg).

    ok is True only when the status line contains '200' and a header line contains
    'video/mp2t', case-insensitively. Split out of check_fanout_feed_port_bound so
    the decision is unit-testable without a real socket."""
    text = response_bytes.decode("latin-1", errors="replace")
    lines = text.split("\r\n")
    if not lines or "200" not in lines[0]:
        status = lines[0] if lines else "<empty>"
        return False, f"status line: {status!r}"
    for line in lines[1:]:
        if not line:
            break   # end of headers
        if "video/mp2t" in line.lower():
            return True, ""
    return False, f"no video/mp2t header in response headers: {lines[1:8]!r}"


def check_fanout_feed_port_bound(ctx):
    """Fan-out mode: the relay (not streamlink) must own the feed-A port and
    serve HTTP/1.0 200 with Content-Type: video/mp2t.

    Connects raw TCP to ctx.fanout_feed_port, sends a minimal GET, reads the
    response headers, and asserts the relay owns the port and emits the right
    Content-Type. The body is expected to be empty, because the no-op stub
    streamlink produces no TS; the header alone proves ownership.

    Skips when ctx.fanout_feed_port is None, meaning no fan-out relay in this
    run."""
    port = getattr(ctx, "fanout_feed_port", None)
    if port is None:
        return CheckResult("fanout_feed_port_bound", "skip", "no fanout relay")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(b"GET / HTTP/1.0\r\n\r\n")
            # Read until the header/body boundary; the body is empty or minimal.
            data = b""
            while len(data) < 4096:
                chunk = s.recv(1024)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data:
                    break
        finally:
            s.close()
    except OSError as exc:
        return CheckResult("fanout_feed_port_bound", "fail",
                           f"TCP connect to 127.0.0.1:{port} failed: {exc}")
    ok, msg = _fanout_http_ok(data)
    if not ok:
        return CheckResult("fanout_feed_port_bound", "fail", msg)
    return CheckResult("fanout_feed_port_bound", "pass",
                       f"relay owns port {port}, Content-Type: video/mp2t")


def check_health_monitor_v3(ctx):
    """Health Monitor /health-monitor/data must include the v3 band + series fields.

    In synthetic mode OBS, Tailscale and Companion are absent, so the values are
    None or empty, but the keys must be present in the respective maps.

    bands: stream_active, funnel_ok, tailscale_up, companion_ok
    series: stream_kbps, obs_cpu_pct
    """
    st, blob = _get_json(ctx.relay_url + "/health-monitor/data")
    if st != 200:
        return CheckResult("health_monitor_v3", "fail", f"/health-monitor/data HTTP {st}")
    if not isinstance(blob, dict):
        return CheckResult("health_monitor_v3", "fail", f"body is not a dict: {type(blob)}")
    err = blob.get("error")
    if err:
        return CheckResult("health_monitor_v3", "fail", f"health monitor disabled: {err}")
    bands = blob.get("bands") or {}
    for f in ("stream_active", "funnel_ok", "tailscale_up", "companion_ok"):
        if f not in bands:
            return CheckResult("health_monitor_v3", "fail", f"missing band {f!r}")
    series = blob.get("series") or {}
    for f in ("stream_kbps", "obs_cpu_pct"):
        if f not in series:
            return CheckResult("health_monitor_v3", "fail", f"missing series {f!r}")
    return CheckResult("health_monitor_v3", "pass", "")


def check_intermission_page(ctx):
    """GET /intermission serves the chat-box overlay page, marked by id="ichat"."""
    st, body, _ = http_request(ctx.relay_url + "/intermission")
    ok = st == 200 and b'id="ichat"' in body
    if ok:
        msg = ""
    elif st != 200:
        msg = f"status={st}"
    else:
        msg = 'marker id="ichat" missing'
    return CheckResult("intermission_page", "pass" if ok else "fail", msg)


def check_program_audio_stream(ctx):
    """On-air program-audio monitor: assert the availability probe
    `GET /preview/program-audio?probe=1`, not the real stream endpoint.

    The probe returns a finite JSON body `{"available": true}` with HTTP 200 when
    the service is present and fan-out is on, without starting the encoder. The
    non-probe endpoint is an endless audio/mpeg response, and under the no-op
    streamlink stubs no audio ever flows, so reading its body would hang this
    harness.

    The main synthetic relay runs direct-serve, so its probe always 404s. This
    check targets the dedicated fan-out relay (ctx.fanout_relay_url) instead, and
    skips when that relay is not part of the run.

    The disabled path is covered by the pure unit tests in
    tests/test_program_audio.py and the console-auth tests in
    tests/test_console.py."""
    base = getattr(ctx, "fanout_relay_url", None)
    if base is None:
        return CheckResult("program_audio_stream", "skip", "no fanout relay")
    st, body, headers = http_request(base + "/preview/program-audio?probe=1")
    if st != 200:
        return CheckResult("program_audio_stream", "fail",
                           f"expected 200, got {st}")
    try:
        data = _json.loads(body or b"null")
    except (ValueError, TypeError):
        return CheckResult("program_audio_stream", "fail",
                           f"non-JSON probe body: {body[:120]!r}")
    if not isinstance(data, dict) or data.get("available") is not True:
        return CheckResult("program_audio_stream", "fail",
                           f"unexpected probe body: {data!r}")
    return CheckResult("program_audio_stream", "pass", "probe reports available")


SYNTHETIC_CHECKS = [
    check_status_ok,
    check_cockpit_requires_token,
    check_cockpit_accepts_token,
    check_cockpit_404_when_disabled,
    check_cockpit_tally,
    check_cockpit_timer_renders,
    check_chat_round_trip,
    check_submission_pending,
    check_event_title_round_trip,
    check_cc_api_cockpit,
    check_enable_preserves_keys,
    check_health_monitor_v3,
    check_fanout_feed_port_bound,
    check_intermission_page,
    check_program_audio_stream,
]

# Real-league mode, local only: the safe subset for a copied profile. The two
# round-trip checks write only relay-local state (chat.json, event.json) with no
# external push, so they are harmless against a throwaway copy.
# It excludes check_submission_pending, whose POST /cockpit/submit could ping the
# league's real Discord webhook, and check_cockpit_404_when_disabled, which needs
# a second disabled relay.
REAL_LEAGUE_CHECKS = [
    check_status_live,
    check_cockpit_requires_token,
    check_cockpit_accepts_token,
    check_cockpit_tally,
    check_cockpit_timer_renders,
    check_chat_round_trip,
    check_event_title_round_trip,
    check_cc_api_cockpit,
    check_enable_preserves_keys,
]
