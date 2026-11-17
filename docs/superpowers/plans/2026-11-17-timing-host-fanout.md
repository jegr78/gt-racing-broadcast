# Timing host fan-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An optional per-league Cloudflare Worker fans the public live timing data out to viewers over WebSocket, fed by one persistent WebSocket from the relay, with a producer switch between the hosted and the Funnel path.

**Architecture:** A stdlib publisher thread in the relay sends `driver_race.public_view` (the bytes `timing_feed.TickBody` builds) once per tick over TLS WebSocket to a Durable Object room per timing link id; the room fans it out to viewer sockets (Hibernation API). racecast deploys the Worker through the Cloudflare REST API with the league's token. `timing.html` stays one page with two transports.

**Tech Stack:** Python 3.11+ stdlib (`socket`, `ssl`, `obs_ws` framing), Cloudflare Workers + SQLite Durable Objects (ES modules), `deno test` for the Worker.

**Spec:** `docs/superpowers/specs/2026-11-17-timing-host-fanout-design.md`

## Global Constraints

- Edit only under `src/` (plus `tests/`, `tools/`, `.github/`, `docs/`); `dist/` and `runtime/` are generated.
- Scripts and docs English only; argparse `help=` ASCII only; text-mode `subprocess` passes `errors="replace"`.
- Outbound HTTP from `racecast.py`, `ui_server.py`, `src/scripts/*` goes through `src/scripts/http_util.py`. The relay's publisher socket is raw `socket`+`ssl` (like the Twitch IRC reader) and is not HTTP.
- No secrets, account ids, real hostnames or machine paths in committed files or tests.
- Workers Free only: SQLite Durable Objects (`new_sqlite_classes`), `*.workers.dev`, no custom domain.
- Hard boundary: `driver_race`, `/drivers/dashboard` and every other `/drivers/*` route are never paced, delayed or routed through the host; a failed or slow host never blocks a relay thread that serves anything else.
- Tick 2 s; ack at least every 5 states; no ack for 15 s = dead connection; room persists state at most every 30 s; orphaned room ends after 30 min without publisher; ended tombstone purged after 7 days; viewer fallback after 3 failed reconnects or 20 s of total silence on an open socket.
- Every new CLI action gets its Control Center counterpart in the same change; every changed Control Center view or desk gets its wiki screenshot refreshed in the same change.
- Run `python3 tools/lint.py` after each Python change.

## Review Focus

1. Relay restart mid-event: hosted viewers must stay on the hosted page (`publisher: false`), never redirect to the Funnel page that is down at that moment. Test in Task 6 (Worker) and Task 7 (page decision).
2. Link renewal (`event timing --new`) while viewers are connected: the old room gets `end`, its viewers see "ended", the publisher connects to the new room without a relay restart. Test in Task 4.
3. Host unreachable at relay start or DNS failure: publisher backs off (cap 30 s), logs one WARNING per distinct error, never blocks `/drivers/*`. Test in Task 4 and the boundary test in Task 5.
4. Switching to `funnel` while Funnel is off (no MagicDNS host): the switch is refused with an error instead of sending viewers to `null`. Test in Task 5.
5. A large public body (60-entry endurance grid, over 64 KiB uncompressed) is framed with the 64-bit length and reaches the room intact. Test in Task 3.

---

### Task 1: Verify the REST deploy contract against a real account (throwaway)

The docs left two points open: the exact `migrations` shape and whether an update upload without new migrations needs a tag. Settle both before writing the deploy client.

**Files:**
- Create (scratchpad only, not committed): `<scratchpad>/cf-rest-probe.py`
- Modify: `docs/superpowers/specs/2026-11-17-timing-host-fanout-design.md` (record the verified request shapes under "Deploy")

- [ ] **Step 1:** Ask the user for a Cloudflare account id and an API token with Workers Scripts Edit for a test account (never written into the repo).
- [ ] **Step 2:** With a stdlib script, `GET /accounts/{id}/workers/subdomain`; record the response shape (expected: `result.subdomain`).
- [ ] **Step 3:** Upload a minimal Worker + `TimingRoom` class with `migrations: {"new_tag": "v1", "new_sqlite_classes": ["TimingRoom"]}`, a `durable_object_namespace` binding, a `secret_text` and a `plain_text` binding. Expected: `success: true`.
- [ ] **Step 4:** Upload again (a) without `migrations`, (b) with `{"old_tag": "v1", "new_tag": "v1"}`. Record which one succeeds and the error text of the other.
- [ ] **Step 5:** `POST .../scripts/{name}/subdomain {"enabled": true}`; open `https://<name>.<subdomain>.workers.dev/` and see the Worker answer.
- [ ] **Step 6:** Find whether a script's current migration tag is readable (`GET .../scripts/{name}/settings`); record it.
- [ ] **Step 7:** Delete the probe script; write the verified shapes into the spec; commit the spec.

```bash
git add docs/superpowers/specs/2026-11-17-timing-host-fanout-design.md
git commit -m "docs(spec): verified Cloudflare REST deploy shapes (#1161)"
```

### Task 2: League keys, injection, masking

**Files:**
- Modify: `src/scripts/config.py` (`ResolvedConfig` + `resolve`)
- Modify: `src/racecast.py` (`_profile_env_pairs`)
- Modify: `src/scripts/profile_admin.py` (template comment block, `profile show` masking)
- Modify: `src/ui/` league settings view (fields for account id and token, read-only key and URL)
- Test: `tests/test_config.py`, `tests/test_profile_admin.py` (use the existing files that cover these functions)

**Interfaces:**
- Produces: `ResolvedConfig.timing_host_account_id`, `.timing_host_api_token`, `.timing_host_publish_key`, `.timing_host_url` (str, "" when unset); env `RACECAST_TIMING_HOST_ACCOUNT_ID`, `RACECAST_TIMING_HOST_API_TOKEN`, `RACECAST_TIMING_HOST_PUBLISH_KEY`, `RACECAST_TIMING_HOST_URL`; `RACECAST_PROFILE_ENV` = absolute path of the active `profile.env` (for the relay's live reload).

- [ ] **Step 1: Failing tests**
  - `t_timing_host_keys_resolve`: a profile.env with the four `TIMING_HOST_*` keys resolves to the four fields.
  - `t_timing_host_keys_injected`: `_profile_env_vars(rc)` contains the four prefixed keys and `RACECAST_PROFILE_ENV` ends with `profile.env`.
  - `t_profile_show_masks_timing_secrets`: output of `profile show` contains neither the token nor the publish key value.
- [ ] **Step 2:** Run, expect FAIL (missing attributes).
- [ ] **Step 3:** Implement. Settings view: two inputs saved through the existing profile.env write path; key and URL displayed read-only.
- [ ] **Step 4:** Run the tests and `python3 tools/lint.py`; expect PASS.
- [ ] **Step 5:** Refresh the settings view screenshot (`wiki-screenshots` skill) and commit.

```bash
git commit -m "feat(timing): league keys for the timing host (#1161)"
```

### Task 3: Protocol helpers and a TLS WebSocket client

**Files:**
- Create: `src/scripts/timing_host.py` (pure, stdlib, relay-importable)
- Create: `src/scripts/ws_client.py`
- Modify: `src/scripts/obs_ws.py` (`handshake_request` gains `path="/"` and `headers=None`, defaults keep today's bytes)
- Modify: `tools/build-binary.py` (`--hidden-import` for both new modules)
- Test: `tests/test_timing_host.py`, `tests/test_ws_client.py`, `tests/test_obsws.py`

**Interfaces:**
- Produces in `timing_host`:
  - `PROTOCOL_V = 1`, `LINK_ID_RE` (the format `timing_link`/`driver_invite` ids have)
  - `script_name(profile: str) -> str` (lowercase, `[a-z0-9-]`, prefix `racecast-timing-`, max 63 chars)
  - `publish_url(host_url: str, link_id: str) -> str` -> `wss://<host>/t/<id>/publish?v=1`
  - `viewer_url(host_url: str, link_id: str) -> str` -> `https://<host>/t/<id>`
  - `state_frame(seq: int, age_s: float, body: bytes, fallback: str | None) -> str` (embeds `body` without re-parsing)
  - `end_frame() -> str`, `moved_frame(to: str) -> str`
  - `parse_host_frame(text: str) -> dict | None` (only `{"t":"ack","seq":int,"viewers":int,"conns_today":int}`; anything else None)
  - `read_settings(profile_env_path: str) -> Settings | None`, `Settings = namedtuple("Settings", "url key")`; None when either is empty or the file is missing
- Produces in `ws_client`: `class WsClient` with `connect(url: str, headers: dict, timeout: float, ssl_context=None) -> WsClient` (classmethod; `ws://` plain, `wss://` TLS with verification), `send_text(text: str) -> None`, `recv_text(timeout: float) -> str | None` (None on timeout; answers ping with pong; raises `ConnectionError` on close), `close() -> None`.

- [ ] **Step 1: Failing tests**
  - `t_state_frame_embeds_body`: `json.loads(state_frame(3, 1.5, b'{"v":1}', None))` == `{"t":"state","seq":3,"age_s":1.5,"data":{"v":1},"fallback":None}`.
  - `t_parse_host_frame_rejects_unknown`: `parse_host_frame('{"t":"x"}') is None`, `parse_host_frame("not json") is None`.
  - `t_script_name_is_valid`: `script_name("ERF Endurance_2")` matches `^racecast-timing-[a-z0-9-]{1,47}$`.
  - `t_read_settings_needs_url_and_key`: file with only `TIMING_HOST_URL` -> None.
  - `t_handshake_request_default_unchanged`: `handshake_request("h", 1, "k")` equals today's bytes.
  - `t_ws_client_roundtrip_large_frame`: against a loopback test server (thread, plain `ws://`), send a 70,000-byte text and receive it echoed; the server sees the `Authorization` header.
  - `t_ws_client_recv_timeout_returns_none`.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement; `ws_client` reuses `obs_ws.encode_frame`, `decode_frame`, `handshake_request`, `parse_handshake`.
- [ ] **Step 4:** Run tests + lint; expect PASS.
- [ ] **Step 5:** Commit.

```bash
git commit -m "feat(timing): publish protocol helpers and a stdlib TLS WebSocket client (#1161)"
```

### Task 4: Publisher

**Files:**
- Create: `src/scripts/timing_publish.py`
- Modify: `tools/build-binary.py` (`--hidden-import timing_publish`)
- Test: `tests/test_timing_publish.py`

**Interfaces:**
- Consumes: `timing_host.*`, `ws_client.WsClient.connect`.
- Produces: `class Publisher(settings_fn, link_fn, body_fn, served_fn, fallback_fn, connect=WsClient.connect, clock=time.monotonic, log=LOG)`:
  - `settings_fn() -> timing_host.Settings | None`; `link_fn() -> str | None` (link id); `body_fn() -> (bytes, float)` (body, age_s); `served_fn() -> "hosted" | "funnel"`; `fallback_fn() -> str | None` (Funnel page URL).
  - `step() -> float` one iteration, returns seconds to wait (testable without threads).
  - `run(stop: threading.Event) -> None`, `reload() -> None` (re-read settings on next step), `status() -> dict` with keys `configured, connected, last_ack_age_s, viewers, conns_today, msgs_today, last_error`.
  - Constants `TICK_S = 2.0`, `ACK_TIMEOUT_S = 15.0`, `BACKOFF_MAX_S = 30.0`.

- [ ] **Step 1: Failing tests** (fake connection object recording sent frames, fake clock)
  - `t_sends_one_state_per_tick`: two steps 2 s apart send seq 1 and 2.
  - `t_no_settings_no_connection`: `settings_fn` None -> `connect` never called, `status()["configured"] is False`.
  - `t_ack_timeout_reconnects`: no ack for 15 s -> connection closed, next step reconnects.
  - `t_link_change_ends_old_room`: link id changes A -> B: `end` sent on A, then a new connection to B's publish URL.
  - `t_link_cleared_ends_room`: link id becomes None -> `end` sent, connection closed, no reconnect.
  - `t_served_funnel_sends_moved_once`: `served_fn` switches to `funnel` -> exactly one `moved` frame with the fallback URL.
  - `t_connect_failure_backs_off_and_logs_once`: three failing connects with the same error -> waits grow to at most 30 s, one WARNING.
  - `t_msgs_today_counts_sent_frames`.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement. Every socket call has a timeout ≤ 5 s so a hung host never stalls the loop past one tick.
- [ ] **Step 4:** Run tests + lint; expect PASS.
- [ ] **Step 5:** Commit.

```bash
git commit -m "feat(timing): relay publisher for the timing host (#1161)"
```

### Task 5: Relay wiring, served path, links

**Files:**
- Modify: `src/scripts/timing_link.py` (`served(path) -> str | None`, `set_served(path, value) -> None`; stored in the link file, default `"hosted"` when a host is configured)
- Modify: `src/relay/racecast-feeds.py` (start the publisher thread; `POST /drivers/timing-host/reload`, tailnet only, not in any `/console` allowlist; extend `GET /drivers/timing-link`; add `moved_to` to the Funnel timing data when served is `hosted`)
- Modify: `src/racecast.py` (`event_timing_data` returns `hosted`, `served`, `host` status; `racecast event timing --serve hosted|funnel`; nudge the running relay via `/drivers/timing-host/reload` after a switch)
- Modify: `src/ui/ui_server.py` (`POST /api/event-timing {"serve": ...}`), `src/ui/control-center.html` (Home row: served link on top, other link below, host status, viewer count, today's request estimate `conns_today + msgs_today / 20`, switch), `src/console/driver-desk.js` (same order and status)
- Modify: `src/relay/CLAUDE.md` (publisher and served path paragraph)
- Test: `tests/test_driver_routes.py`, `tests/test_timing_link.py` (create if absent), `tests/test_racecast.py` (or the file that covers `event_timing_data`), `tests/test_ui_server.py`

**Interfaces:**
- Consumes: `Publisher` (Task 4), `timing_host.read_settings(os.environ["RACECAST_PROFILE_ENV"])`.
- Produces: `GET /drivers/timing-link` -> `{active, id, served, hosted_url, host: Publisher.status()}`; `event_timing_data(...)` -> adds `hosted`, `served`, `host`; Funnel `/console/timing/data` JSON gains `moved_to` (hosted link) only while served is `hosted` and a host is configured.

- [ ] **Step 1: Failing tests**
  - `t_timing_link_served_roundtrip`: `set_served(p, "funnel")`; `served(p) == "funnel"`; `create(p)` resets it.
  - `t_timing_link_route_reports_host`: with a fake publisher, `/drivers/timing-link` carries `served`, `hosted_url`, `host`.
  - `t_funnel_data_moved_to_when_hosted`: served `hosted` -> data JSON has `moved_to`; served `funnel` -> no key.
  - `t_serve_funnel_refused_without_funnel_host`: `event_timing_data(serve="funnel")` with no MagicDNS -> `ok: False`.
  - `t_internal_dashboard_routes_ignore_a_dead_host` (extends the #1160 boundary test): publisher connect hangs/fails; `/drivers/dashboard` still returns every new snapshot immediately.
  - `t_timing_host_reload_not_reachable_over_console`: `/console/drivers/timing-host/reload` is refused by the gate.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests, `python3 tests/test_pov.py`, lint; expect PASS.
- [ ] **Step 5:** Visually verify the Home row and both desks (`ui-visual-verification` skill), refresh `cc-home.png` and the desk screenshots (`wiki-screenshots`), commit.

```bash
git commit -m "feat(timing): served path switch and hosted link in the desks and the Control Center (#1161)"
```

### Task 6: The Worker and its tests

**Files:**
- Create: `src/timing-host/worker.js` (default export `{ fetch }`, `export class TimingRoom`)
- Create: `tests/timing-host/worker_test.js` (deno, stub `DurableObjectState`, `WebSocketPair`, storage, alarms)
- Create: `tests/test_timing_worker.py` (runs `deno test tests/timing-host/`; skips when deno is missing unless `RACECAST_REQUIRE_DENO=1`; `subprocess.run(..., errors="replace")`)
- Modify: `.github/workflows/ci.yml` (test job: `denoland/setup-deno` pinned by SHA, env `RACECAST_REQUIRE_DENO=1`)
- Modify: `tools/build.py`, `tools/build-binary.py` (ship `src/timing-host/`)

**Interfaces:**
- Consumes: frames from Task 3; env bindings `ROOMS` (DO namespace), `PUBLISH_KEY` (secret), `RACECAST_VERSION` (plain text), page module `./timing.html` (text).
- Produces routes: `GET /t/<id>` page (404 "unknown timing link" for an unknown room, "this timing link has ended" for an ended one), `GET /t/<id>/ws` viewer upgrade, `GET /t/<id>/publish?v=1` publisher upgrade (401 wrong key before any DO call, 400 unknown `v`), `GET /version` -> `RACECAST_VERSION`. A malformed id is 404 without a DO call.

- [ ] **Step 1: Failing deno tests**
  - `malformed id never reaches a room`; `wrong publish key is 401`; `unknown room 404s the page and the socket`.
  - `first publish makes the room live and fans out to every viewer`.
  - `acks at least every 5 states with viewers and conns_today`.
  - `publisher close sends publisher:false, reconnect sends publisher:true` (Review Focus 1).
  - `moved is forwarded to viewers`.
  - `end forwards end, closes viewers, deletes state, keeps a tombstone; alarm purges it after 7 days`.
  - `30 min without publisher ends the room via alarm`.
  - `state is persisted at most every 30 s`.
- [ ] **Step 2:** `python3 tests/test_timing_worker.py`, expect FAIL.
- [ ] **Step 3:** Implement with `ctx.acceptWebSocket` and tags `viewer`/`publisher`; viewer messages are ignored.
- [ ] **Step 4:** Run, expect PASS; `python3 tools/run-tests.py` still green without deno (skip).
- [ ] **Step 5:** Commit.

```bash
git commit -m "feat(timing): Cloudflare Worker and Durable Object room (#1161)"
```

### Task 7: Viewer page with two transports

**Files:**
- Modify: `src/console/timing.html`
- Test: `tests/timing-host/page_test.js` (deno; loads the page's `<script>` and tests its pure decision function), plus the existing Python tests for the page

**Interfaces:**
- Consumes: frames from Task 6; Funnel data `moved_to` from Task 5.
- Produces: in the page, pure `fallbackDecision(s) -> "stay" | "redirect"` where `s = {failedReconnects, silentForS, publisherUp, fallback}`; the page picks WebSocket when served under `/t/<id>` and polling otherwise.

- [ ] **Step 1: Failing tests**
  - `3 failed reconnects with a fallback redirects`; `20 s silent open socket redirects`.
  - `publisher down stays` (Review Focus 1); `null fallback stays`.
  - `moved on the hosted page redirects to its target`; on the Funnel page `moved_to` redirects on the next poll.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement; keep the countdown and data-age display, fed from `age_s` plus local receive time; show "reconnecting" while the publisher is down.
- [ ] **Step 4:** Run, expect PASS; render the page in both modes (`ui-visual-verification`).
- [ ] **Step 5:** Commit.

```bash
git commit -m "feat(timing): hosted page over WebSocket with Funnel fallback (#1161)"
```

### Task 8: Deploy and status (CLI and Control Center)

**Files:**
- Create: `src/scripts/timing_host_deploy.py`
- Modify: `src/racecast.py` (`racecast timing-host deploy|status`, top-level help line), `src/ui/ui_ops.py` and `src/ui/ui_server.py` (`GET /api/timing-host` status, `POST /api/timing-host {"action":"deploy"}`), the league settings view (Deploy and Status buttons next to the fields from Task 2)
- Modify: `tools/build-binary.py` (`--hidden-import timing_host_deploy`)
- Test: `tests/test_timing_host_deploy.py`, `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 1's verified shapes; `http_util`; `timing_host.script_name`; `_set_env_key` in `racecast.py`.
- Produces: `bundle_version(worker_src: bytes, page_src: bytes) -> str` (sha256 hex[:16]); `build_metadata(publish_key: str, version: str, first: bool) -> dict`; `multipart(metadata: dict, parts: list[tuple[str, bytes, str]]) -> tuple[bytes, str]`; `deploy(account_id, token, profile, worker_src, page_src, publish_key, request=None) -> dict` `{ok, url, error}`; `status(account_id, token, profile, worker_src, page_src, request=None) -> dict` `{ok, deployed, up_to_date, url, error}`.

- [ ] **Step 1: Failing tests** (fake `request` recording method, path, headers, body)
  - `t_missing_subdomain_explains_dashboard`: subdomain empty -> `ok False`, error names Workers & Pages, Account details, Subdomain.
  - `t_first_deploy_sends_migration_and_bindings`: metadata has `main_module`, the DO binding, `secret_text` `PUBLISH_KEY`, `plain_text` `RACECAST_VERSION`, migration per Task 1.
  - `t_update_deploy_uses_verified_migration_form` (Task 1 result).
  - `t_deploy_enables_workers_dev_and_returns_url`.
  - `t_cli_deploy_writes_url_and_generates_key_once`: second deploy keeps the key.
  - `t_status_reports_update_needed` when the deployed version differs.
  - `t_deploy_nudges_running_relay` (`/drivers/timing-host/reload` called when the relay runs).
  - `t_api_timing_host_routes` in `test_ui_server.py`.
  - The token never appears in any log line or error message.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run tests + lint; `racecast timing-host --help` works under `LANG=C`.
- [ ] **Step 5:** Refresh the settings view screenshot, commit.

```bash
git commit -m "feat(timing): deploy the timing host through the Cloudflare API (#1161)"
```

### Task 9: Docs, full gates, real-account check

**Files:**
- Modify: `src/docs/wiki/Driver-Telemetry.md` (hosted timing: account, subdomain, token permission, deploy, served switch, fallback, quota numbers and the per-account note), `src/relay/CLAUDE.md`, `src/scripts/CLAUDE.md` (new league keys), `CLAUDE.md` (security boundary: the relay now opens one outbound WebSocket to the league's host; nothing new is funnelled)

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tools/build.py`, `python3 tools/e2e.py`; all exit 0.
- [ ] **Step 3:** Real account by hand: deploy, `event start`, hosted page shows data, a second browser counts as a viewer in the Control Center, `relay stop` + `relay start` keeps the hosted viewer with "reconnecting", switch to `funnel` redirects the hosted viewer, switch back redirects the Funnel viewer, `event stop` shows "ended".
- [ ] **Step 4:** Commit.

```bash
git commit -m "docs(timing): hosted live timing in the wiki and the relay notes (#1161)"
```
