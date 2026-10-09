# IRO Control Center — Design

**Date:** 2026-06-07
**Status:** Approved

## Problem

The `iro` CLI is the producer's only entrypoint, and a terminal is alien to
most broadcast producers. Onboarding a new producer today means walking them
through shell commands for everything: first-time setup (`init`, installs),
event-day operation (`event start/stop`, `status`, `cookies`), and
troubleshooting (`logs`). The director already has a friendly web surface
(`/panel`); the producer has nothing.

Goal: a local **Control Center** web app — full CLI parity, started by
double-clicking a second binary (`iro-ui`), no terminal required. One shared
code base for CLI and UI: the UI must not duplicate command logic.

## Decisions (settled during brainstorming)

| Question | Decision |
|---|---|
| UI technology | Local web app served by the binary itself: stdlib `ThreadingHTTPServer` + one static HTML page (vanilla JS, inline CSS — the `director-panel.html` model). Opens in the default browser via `webbrowser.open()`. pywebview/Tauri/Electron/Textual rejected: pip deps, extra toolchains, or wrong audience — the stdlib server is the only option that satisfies every project rule. |
| Entrypoint | A **second PyInstaller binary `iro-ui`** built from the same `src/` tree (Windows: `--noconsole`; macOS: `.app` bundle; Linux: plain binary). Plus `iro ui` as a CLI subcommand running the same server function (debugging, parity). |
| Scope | **Full CLI parity** — not just event-day essentials. Onboarding new producers without a terminal is the primary motivation, so setup (`init`, installs) is in scope from the start, delivered in phases. |
| `iro init` in the UI | A **real step-by-step wizard** (option a), not a log console with an stdin bridge. The init plan/skip/gate logic is decomposed into discrete steps the UI drives one at a time. |
| Process lifecycle | **Quit button** ends the server (option a). Closing the browser tab leaves it running; double-clicking again detects the running instance and just reopens the browser. No auto-exit in v1. Relay/streams/Companion are independent daemons either way. |
| Network exposure | v1 binds **localhost only**. The bind point is structured like the relay's (`auto`-capable) and an auth hook is designed in (`IRO_UI_PASSWORD` → session cookie) — both inactive until a v2 "remote support over Tailscale" feature. |
| Port | Default **8089**; `IRO_UI_PORT` in `.env` overrides it when another app permanently occupies 8089. |
| Shared basis | Two operation classes (see below): structured in-process functions for quick ops, and subprocess jobs (re-invoking the `iro` binary) for long-running ops. No logic duplication; the CLI handlers and the UI API call the same code. |

## Architecture

### Startup and lifecycle

`src/iro_ui.py` is the second entrypoint:

1. **Single-instance check:** probe `127.0.0.1:<port>` for a running Control
   Center (a `/api/ping` endpoint identifying itself). If one answers, open
   the browser at it and exit 0.
2. Start the HTTP server on `127.0.0.1:<port>` (port = `IRO_UI_PORT` from
   `.env`/environment, default 8089).
3. `webbrowser.open("http://127.0.0.1:<port>/")`.
4. Run until the Quit endpoint (`POST /api/quit`, confirmed in the UI) shuts
   the server down.

If the port is occupied by a foreign process (probe answers but not with the
Control Center signature, or bind fails), the binary must not die silently —
windowed builds have no terminal. It writes the error to a log file under
`runtime/` and surfaces a native message via stdlib means (macOS `osascript`
dialog, Windows `ctypes.windll.user32.MessageBoxW`, Linux stderr), pointing
at `IRO_UI_PORT` as the fix.

### Operations model — one logic, two presentations

The core problem: today's handlers (`relay_status()`, `event_status()`, …)
print to stdout. The shared basis splits data from presentation:

- **Structured ops** (all `status` variants, init steps, `obs refresh`,
  tailscale status): refactored into functions returning JSON-serializable
  **dicts**. The CLI renders them as text; the UI API returns them as JSON.
  In-process for both callers.
- **Jobs** (install-tools/-apps, graphics, media, cookies, setup, preflight,
  event start/stop, export companion, and all service start/stop/restart
  actions — uniform handling, their output is worth capturing): the UI
  spawns them as a **subprocess of the `iro` binary itself** — the same
  re-invocation mechanism daemons already use in frozen mode — and streams
  combined stdout/stderr live. Subprocess rather than an in-process thread
  because `sys.stdout` is process-global (parallel jobs would interleave
  output) and a subprocess gives clean capture plus cancellation (kill).
  Exit code 0 = success. In dev (non-frozen) the job command is
  `python3 src/iro.py <args>`.

New modules under `src/ui/`:

- `ops.py` — operation registry: name, parameters, invocation kind
  (structured call vs. job argv). Single source the server routes from.
- `jobs.py` — job manager: spawn, per-job ring buffer of output lines,
  status (running/exit code), cancel. At most one concurrent job per
  operation; conflicting starts return a structured error.
- `server.py` — `ThreadingHTTPServer` + handler: static page, JSON API,
  SSE streams. Follows the relay's `make_handler()` pattern.
- `control-center.html` — the single static page.

### HTTP API (all JSON; errors always `{ok: false, error: "..."}`)

- `GET /` — the page. `GET /api/ping` — instance signature + version.
- `GET /api/status` — aggregate dashboard data (relay, companion, streams,
  tailscale, OBS reachability, event readiness summary).
- `POST /api/op/<name>` — run a structured op or start a job (body: params).
  Job start returns `{job_id}`.
- `GET /api/jobs/<id>` — job status (`?tail=N`, at most 200, adds the last N output
  lines); `POST /api/jobs/<id>/cancel`. A refused `POST /api/op/<name>` (409) names the
  job of that op that already runs in `job_id`.
- `GET /api/jobs/<id>/stream` — **SSE** live output (`text/event-stream`;
  works under the threaded server — one held connection per client).
- `GET /api/logs/<service>/stream` — SSE tail of the existing `runtime/`
  logs (relay, companion, streams).
- `GET /api/init/plan` — the init wizard plan (reuses the existing
  plan/skip-detection logic); `POST /api/init/step/<id>` — run one step.
- `POST /api/quit` — shut down the Control Center.

SSE is the chosen transport; a long-poll fallback (`/api/jobs/<id>/output?since=N`)
is trivial to add if a browser/proxy combination misbehaves.

### Frontend — one page, four areas

Same construction as `director-panel.html`: one file, inline CSS/JS, no
framework, no build step. English-only text. Areas:

- **Dashboard** — aggregate status, polls `/api/status` every ~3 s. Links to
  the director panel (`http://127.0.0.1:8088/panel`) when the relay runs.
- **Event Day** — event start (with optional `--stint N` field) / stop,
  obs refresh, cookies refresh (browser dropdown), relay/streams/companion
  start/stop/restart.
- **Setup** — the init wizard (plan view: per-step done/pending/skipped;
  gates become confirmation dialogs; long steps run as jobs with live log)
  plus the individual actions: install-tools/-apps (with `--update`),
  graphics, media, setup export, export companion, preflight.
- **Logs** — tail of the service logs and live output of running jobs.

Destructive or heavyweight actions (any stop, installs, quit) require a
confirmation click. The UI describes mechanism only — no invented broadcast
procedure (house rule).

## Error handling

- Failed job → non-zero exit visible in the UI, log retained, action button
  re-enabled.
- Relay unreachable → dashboard shows "stopped" (same semantics as
  `iro status`), not an error state.
- Port conflict / bind failure → native dialog + log file (see Startup).
- Webhook/sheet problems surface through the existing status payloads —
  the Control Center adds no new sheet interactions.

## Security

v1 is localhost-only: the unauthenticated API can start installs and stop
services, so it must not be reachable from the LAN or tailnet. The bind and
auth seams exist in the code (one bind function, one auth check returning
"allowed" today) so v2 — Tailscale bind + `IRO_UI_PASSWORD` session cookie
for remote support — changes those two functions only. Documented as out of
scope below.

## Build, release, CI

- `tools/build-binary.py` gains the second target: `iro-ui` from
  `src/iro_ui.py` (Windows `--noconsole`, macOS `BUNDLE` step → `IRO UI.app`,
  Linux plain). Same bundled-`src/` data model; `runtime/` and `.env` stay
  next to the binaries — release archives contain `iro` + `iro-ui` together.
- CI `binary-smoke` exercises both binaries (e.g. `iro-ui --version` plus a
  bind-and-ping smoke).
- `tools/build.py` verify step extends to the new files (English-only,
  no secrets, no shell scripts — unchanged rules).
- `.env.example` documents `IRO_UI_PORT` (and reserves `IRO_UI_PASSWORD`
  as a commented v2 hint).

## Testing

House pattern — stdlib-only runnable test scripts:

- `tests/test_ui_ops.py` — registry contents, structured-op dict shapes
  (the refactored status functions are pure-testable).
- `tests/test_ui_jobs.py` — job lifecycle with fake processes (spawn,
  buffer, exit, cancel, one-per-op rule).
- `tests/test_ui_server.py` — route dispatch and payload shapes, following
  `tests/test_setup.py`'s handler-test pattern; port/bind selection incl.
  `IRO_UI_PORT` override and single-instance probe classification.
- TDD throughout; no real IPs or machine paths.

## Phasing

Too large for one milestone — three phases, each independently shippable:

1. **Foundation** — status refactor (print → dict), `src/ui/` server,
   dashboard, service start/stop, log tails (SSE), quit, `iro ui`
   subcommand. Usable from `python3 src/iro.py ui` / the dev tree.
2. **Jobs** — job manager + every one-shot action (installs, graphics,
   media, cookies, setup, preflight, export companion, event start/stop),
   plus a "Setup & Assets" dashboard section surfacing readiness state:
   cookies freshness and graphics/media completeness vs the sheet's Assets
   tab (the `event status` facts, refactored print → dict like Phase 1 did
   for the service statuses).
3. **Init wizard + packaging** — init step decomposition, wizard UI, the
   `iro-ui` binary (per-OS targets), release/CI integration.

## Out of scope (v1)

- Tailnet exposure + password auth (designed-in seams, v2 feature).
- Tray/menubar icon (would need pip deps; optional later polish).
- Auto-exit on browser close.
- Editing the OBS scene collection or sheet contents from the Control
  Center — the director panel owns during-event sheet control.
