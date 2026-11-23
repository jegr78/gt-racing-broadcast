# Self-healing `relay start` — design

**Date:** 2026-06-23
**Status:** approved (brainstorming), ready for implementation plan

## Problem

The Control Center "Start relay" button (which runs exactly `racecast relay start`)
cannot reliably bring up *the current relay*. A real incident exposed the failure
mode:

- A relay process from a **previous session / profile / pre-upgrade binary** stayed
  alive for days, holding the control port **8088**, because the producer switched
  the active profile and updated the binary without that old daemon ever being
  killed.
- The relay's identity is tracked by a **single un-scoped PID file**
  (`_relay_pid_path()`). Everything keys off `_relay_is_alive()` = "does that PID
  file name a live process". When a later `relay start` overwrote the PID file, the
  earlier process became invisible to the tooling but kept running and kept the
  port.
- The "kill stale relay" Control Center op is `freeport 8088` **without `--force`**
  (`ui_ops.py`). `freeport_cmd` sees `_relay_is_alive()` is true → `freeport_owner`
  returns `"relay"` → `decide_free(owned=True, force=False)` → **refuse**. So it
  never touches the real orphan.
- `relay_start` itself **refuses** when a foreign holder is on 8088
  (`relay_start_port_note`, #273) and tells the user to run `freeport` first — which
  also refuses. **Deadlock.**
- On Windows, `--bind auto` binds both `127.0.0.1` and the Tailscale IP. Two relay
  processes can each grab 8088 on a *different* address, so `pids_on_port(8088)`
  returns two PIDs and requests **split-brain** between old and new (the Funnel,
  which targets `127.0.0.1`, hits whichever holds loopback).

## Goal

`racecast relay start` — and therefore the unchanged Control Center "Start relay"
button — **always converges to the same end state**: exactly one relay of the
current binary listening on 8088, serving the **active** profile. It heals
orphan / split-brain / wrong-profile / dead-PID situations automatically, and it
**never disturbs a healthy active-profile relay**.

Decisions taken during brainstorming:
- **No new button, no confirmation dialog.** The existing "Start" becomes
  self-healing (the user explicitly chose this over a dedicated force-restart
  button).
- **Heal only on a detected defect** — a single healthy active-profile relay is a
  no-op "already running".
- **Automatic + clearly logged** — every heal writes a plain-language line naming
  what was cleared and why (no modal, even for the wrong-profile case).

## Section 1 — Behavior contract

`relay start` classifies the current state from these signals:

| Signal | Source |
|---|---|
| active profile | `_active_profile_name()` |
| PID-file PID + alive? | `sv.read_pid` / `sv.pid_alive` |
| actual listeners on 8088 | `pt.pids_on_port(8088)` (≥2 distinct PIDs ⇒ split-brain) |
| running relay's profile | `_running_relay_profile()` (the stamp) |
| does /status respond? | `_relay_http_ok()` |

Decision table:

1. **8088 free** → `start` (just start; clear a stale PID file first if its PID is
   dead).
2. **8088 held** AND exactly one distinct PID == PID-file PID, alive, `/status`
   responds, **and** its profile == active → `running`: print "already running",
   **touch nothing** (a healthy live feed is preserved).
3. **8088 held, anything else** — dead PID-file but port held · multiple PIDs
   (split-brain) · holder not responding · holder PID not from the PID file
   (foreign/orphan) · **different profile** → `heal`: hard-kill every holder, then
   start fresh.

`heal` clears **8088 *and* the feed ports 53001–53003** (otherwise Feed A fails to
bind against an orphaned streamlink, exactly the incident's PID 12016), removes the
PID file + profile stamp, then runs the normal fresh-start path. Start no longer
delegates to `freeport` (which refuses while "a relay is alive"); it kills
**by port** itself, breaking the deadlock.

**Empty/unknown `running_profile` is treated as a mismatch** (i.e. it falls into
case 3 / `heal`). A current-binary relay always writes its stamp on start
(`_write_relay_profile_stamp`), so the only relay without a readable stamp is a
pre-stamp / old-binary daemon — replacing it with a fresh current-binary relay is
exactly the intended outcome. The `running` no-op therefore requires a stamp that
**equals** the active profile, never an absent one.

## Section 2 — Implementation shape

Pure classification, impure execution — the existing `relay_start_port_note` /
`decide_free` pattern.

**1. New pure function** in `src/racecast.py`, beside `relay_start`:

```python
def relay_start_plan(*, port_pids, feed_pids, pidfile_pid, pidfile_alive,
                     running_profile, active_profile, http_ok):
    """-> (action, kill_pids, reason)
    action ∈ {"running", "start", "heal"}; reason = short plain-language defect."""
```

- No I/O, no `os.kill` — only the Section 1 table.
- `kill_pids` = `sorted(set(port_pids) | set(feed_pids))` in the heal case, else `[]`.
- `reason` examples: `"dead pidfile but port held"`, `"split-brain: 2 listeners"`,
  `"not responding"`, `"foreign holder"`,
  `"serving 'iro-gtec', active is 'testing'"`.

**2. `relay_start` becomes a thin impure wrapper:**

- Gathers the signals (`pids_on_port(8088)`, each feed port, PID file, stamp,
  `_relay_http_ok()`), calls `relay_start_plan`.
- `"running"` → existing "already running" output + `relay_status`, return.
- `"heal"` → `pt.kill_pid(pid)` for each `kill_pids` (Windows: `taskkill /T /F`;
  POSIX: SIGTERM→SIGKILL + child reap), re-check the ports, remove PID file + stamp,
  emit **one clear log line** with `reason` + killed PIDs, then fall into the fresh
  start.
- `"start"` → fresh start, unchanged
  (`_ensure_active_console_secret`, `_write_relay_profile_stamp`, `start_detached`,
  `_refresh_obs_pages`).

**3. Post-start verification** (new, small): after spawn, wait for `_relay_http_ok`
(like the existing `_refresh_obs_pages(wait=10)`) **and** assert exactly one PID
listens on 8088; otherwise print a warning line rather than silently assuming
success.

Update (#1266): the relay binds its control port before the first Sheet read and
answers `/status` with 503 `{"starting": true, ...}` until it is ready. The plan has a
fourth action, `starting` (our own active-profile relay answers the boot reply): wait
for it, never heal it. A spawned relay that answers "starting" gets up to
`RELAY_BOOT_WAIT_S` instead of the 15 s verify window.

**4. The old refuse path goes away:** `relay_start_port_note` (the "port held by … —
Free it first" message, #273) is replaced by the heal path — the foreign holder is
now cleared instead of blocking the start.

**5. "kill stale relay" button** (`ui_ops.py`, today `freeport 8088` without
`--force`): with self-healing Start it is no longer a required precondition.
Re-point it to `freeport --force 8088 53001 53002 53003` so the manual emergency
brake actually kills instead of refusing. Label unchanged.

## Section 3 — Tests, cross-platform, UI

**Tests (TDD, stdlib-only, run in CI on all three OSes):**

In `tests/test_racecast.py` — `relay_start_plan` as a decision table, one case per
row:
- `port_pids=[]`, dead PID file → `"start"`, no kill.
- one PID == PID file, alive, `http_ok`, profile==active → `"running"`.
- PID file dead, port held → `"heal"`, reason "dead pidfile".
- **two** distinct PIDs on 8088 (split-brain) → `"heal"`, reason "split-brain".
- holder alive but `http_ok=False` → `"heal"`, reason "not responding".
- holder PID ≠ PID file (foreign/orphan) → `"heal"`, reason "foreign holder".
- healthy but `running_profile='iro-gtec'` vs `active='testing'` → `"heal"`, reason
  names both profiles.
- `kill_pids` in the heal case = the union of the 8088 and feed-port PIDs
  (deduplicated/sorted).

In `tests/test_ui_ops.py` — assert `kill-relay` is now
`["freeport", "--force", "8088", "53001", "53002", "53003"]`.

**Cross-platform:** no new OS-specific code — `pids_on_port`
(netstat/lsof/ss/fuser) and `kill_pid` (`taskkill /T /F` / SIGTERM→SIGKILL) are
already cross-platform and tested. The new logic is pure, hence OS-neutral; signal
gathering reuses existing seams.

**UI surface / wiki screenshot:** the Start button is **visually identical** (same
label, same position); only behavior and job/log output change. No visible Control
Center surface changes → **no `cc-*.png` refresh required**. (A persistent "relay
serving profile X" warning badge would need a screenshot — deliberately out of
scope, YAGNI.)

## Deliberately out of scope (YAGNI)

- No new button, no confirmation dialog.
- No rework of the PID-file identity model into port-based identity — healing at the
  start path suffices; touching every caller would be risk without payoff.
- `relay restart` inherits the healing automatically (it calls `relay_stop` +
  `relay_start`).
