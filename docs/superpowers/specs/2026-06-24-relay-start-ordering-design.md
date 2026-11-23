# Relay start ordering — fail fast on a taken control port + fix the empty `profile=` log

**Date:** 2026-06-24
**Status:** Design (approved)
**Area:** the relay startup (`src/relay/racecast-feeds.py`, `main()`).

## Problem

Two issues seen in a real test session's `relay.console.log`:

```
INFO relay starting — profile= bind=auto ports=53001,53002 mode=race schedule=…
INFO Schedule loaded from Google Sheet: 12 stints.
WARNING could not bind 127.0.0.1:8088 — [Errno 48] Address already in use
```

1. **Misleading start ordering.** After the `relay starting` line, `main()` runs every network refresh — POV, Qualifying, Crew, HUD, and the main schedule (`Schedule loaded …`) — and only **then** binds the control port 8088 (line ~5736). So when 8088 is already held by another relay, the log first reads like a *successful* start (`relay starting` / `Schedule loaded`) and only fails afterward, after wasted network round-trips. The relay was started four times in a row this way before the fifth attempt found a free port.

2. **Empty `profile=`.** The start line reads `profile=` (blank) because it logs `os.environ.get("RACECAST_PROFILE", "?")`, which the CLI leaves empty for the relay child. The real, injected identifier is `args.league_name` (from `RACECAST_PROFILE_NAME`).

## Goal

Fail fast and clearly when the mandatory loopback control port is already taken — before any network work and before the misleading logs — and show the actual profile/league name in the start line.

## Non-goals

- No change to the real bind at line ~5736 (it stays as the final, authoritative guard, including the loopback-mandatory abort of issue #84).
- No change to the tailnet/`0.0.0.0` bind behavior, retry logic, or the `freeport` tooling.
- No restructuring of `main()` to bind the real control sockets early (the more invasive option was considered and rejected: the request handler depends on the `Relay` object constructed much later).
  Superseded by #1266: `main()` now binds right after this probe and serves a 503 boot handler until the real handler exists (`src/relay/CLAUDE.md`, "Start order").

## Decisions (resolved during brainstorming)

- **Early probe-check** (not early real-bind): a throwaway socket probe of `127.0.0.1:<http_port>` immediately after the `relay starting` log, aborting cleanly on conflict. Small and low-risk; the real bind remains the final guard against the tiny TOCTOU window.
- **`profile=`** uses `args.league_name` (falling back to `"?"`).

## Design

### Unit — `control_port_available(host, port)` (pure-ish, testable)

```python
def control_port_available(host, port):
    """True if the mandatory loopback control port can be bound right now (no other
    relay holds it). A throwaway probe using the same SO_REUSEADDR semantics as the
    real control server, so its verdict matches what the real bind would see. Returns
    False only on a bind error (port in use / unbindable); the socket is always closed."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
```

- `socket` is already imported by the relay.
- `SO_REUSEADDR` matches the real `HTTPServer` (`allow_reuse_address`), so a port held only by a `TIME_WAIT` socket is not a false positive, while an actively-listening foreign relay still yields `False` (EADDRINUSE).
- Pure enough to unit-test: bind a socket on an ephemeral port in the test and assert `False` for it; assert `True` for a port the test just freed.

### Wiring in `main()`

Immediately after the `relay starting` log (line ~5467), before the first refresh (`pov_source` at ~5471):

```python
    if not control_port_available("127.0.0.1", args.http_port):
        LOG.error("control port 127.0.0.1:%s already in use — another relay is "
                  "probably running; aborting before any startup work.", args.http_port)
        sys.exit(f"Could not bind the control server on 127.0.0.1 port {args.http_port} "
                 f"— another relay is probably already running. Stop it first "
                 f"('racecast relay stop'), then check 'racecast status' / 'racecast preflight' "
                 f"to see what holds the port.")
```

- The `LOG.error` line lands in `relay.console.log` (so the console log no longer shows a misleading `Schedule loaded` before the failure); the `sys.exit` string lands on stderr → `relay.boot.log` (the same clear message the late guard already emits, kept verbatim so the operator sees one consistent message).
- On the normal (free-port) path this adds one negligible bind/close and nothing else changes.

### `profile=` fix

Change the start line (line ~5465-5467) to source the name from the injected arg:

```python
    LOG.info("relay starting — profile=%s bind=%s ports=%s mode=%s schedule=%s",
             (args.league_name or "?"), args.bind, args.ports,
             ("qualifying" if args.qualifying else "race"), csv_url)
```

## Testing (TDD — failing test first)

In `tests/test_bind.py` (the relay bind/port unit tests; it already loads the relay module as `m`):

- `control_port_available` returns `False` for a port currently bound+listening by the test, and `True` for a port the test bound then closed (free again). Use `127.0.0.1` and an OS-assigned ephemeral port (`bind(("127.0.0.1", 0))`) — no hardcoded port, CI-safe.

The `main()` wiring and the `profile=` field are a one-line log change + a guard around the pure helper; they are covered by the helper's unit test (the helper is the only new logic). No new integration test is added for the abort path (it is a `sys.exit` wrapper around the tested helper).

## Files touched

- `src/relay/racecast-feeds.py` — add `control_port_available`; call it early in `main()`; fix the `profile=` field.
- `tests/test_bind.py` (or `tests/test_pov.py`) — unit test for `control_port_available`.
