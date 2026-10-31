# Event Notes — design

**Date:** 2026-06-30
**Status:** approved (brainstorming) → ready for implementation plan
**Persona:** League Owner (maintains notes) → consumed by Director, Commentator, Race Control

## Problem

A league owner wants a place to leave short editorial/operational notes for the crew
for a given event ("sponsor mention in stint 3", "running order", "stream-key reminder"),
maintained centrally and visible inside the live crew tools — without a separate browser
tab or chat scrollback. The crew should be able to glance at the notes on demand from the
Director Panel, the Commentator Cockpit, and the Race Control desk, then dismiss them.

## Scope

In scope:
- One new read-only Google-Sheet tab (`Event Notes`) the owner edits.
- Relay reads it (poll-refreshed) and exposes it on a dual endpoint (tailnet + Funnel).
- A toggleable modal, opened by a button in the top header, in all three console pages.

Explicitly **out of scope** (YAGNI):
- No write path from any console (notes are admin-managed in the Sheet, like the
  `Race Control` / `Cue Preset` vocabulary columns).
- No per-audience targeting — one shared list is shown identically in all three pages.
- No unread/count badge, no auto-open. A plain button that opens a modal on click.
- No section grouping/folding logic.

## Sheet tab schema

New tab **`Event Notes`**. Header row (row 1), columns header-located (order-independent,
mirroring how `Crew`/`Channel` locate their columns):

| Column | Required | Meaning |
|---|---|---|
| `Heading` | optional per row | A bold label line rendered above its note. In practice the owner uses a single heading; there is **no** section-grouping/folding pass — rows render in sheet order. |
| `Note` | yes | The note text. A row with an empty `Note` is skipped. |
| `Priority` | optional | `Info` or empty (case-insensitive) → normal; `Important` → highlighted (accent chip + left border). Any unrecognised value → normal. |

Rendering model: the modal shows a flat list of note rows in sheet order. When a row
carries a `Heading`, that heading is rendered as a bold subheading line above the note.
With a single heading in use this yields one title line followed by the notes.

## Architecture

### Pure parser — `src/scripts/event_notes.py` (new)
`parse_event_notes(csv_text) -> list[dict]`:
- Locate `Heading` / `Note` / `Priority` columns by header (case-insensitive), like the
  existing header-located tab parsers.
- For each data row with a non-empty `Note`, emit `{"heading": str, "note": str,
  "priority": "important"|"info"}` (priority normalised to a lowercase token; unknown →
  `"info"`). `heading` is `""` when absent.
- Missing columns / empty CSV / header-only → `[]` (degrade cleanly, never raise).

*Alternative considered:* folding the parser into `broadcast_chat.py` (which already holds
`parse_channel_tab`). Rejected — a dedicated module with its own test file is consistent
with the project's pure-logic-per-feature pattern (`cue_admin.py`,
`cockpit_submissions.py`) and keeps responsibilities separate.

### Source class — `EventNotesSource` (in `src/relay/racecast-feeds.py`)
A clone of the `ChannelSource` skeleton (`racecast-feeds.py:1770`): `__init__(csv_url,
cache_path)`, `_fetch_text` (urlopen + racecast UA, relay-owned network — exempt from the
http_util UA guard like the other relay sources), `refresh()` (fetch → `parse_event_notes`
→ swap under lock, stamp `last_error`), `get()` (snapshot under lock). Cache file
`runtime/<profile>/event-notes.cache.txt`.

### Relay wiring (`main()`)
- CLI flags: `--event-notes-tab` (default `Event Notes`) and `--no-event-notes`.
- Build the gviz CSV URL from `sheet_id` + the tab name (`.../gviz/tq?tqx=out:csv&sheet=
  <quoted tab>`), construct `EventNotesSource`, warm `refresh()` (non-fatal), pass into
  `Relay`, and register a `poller(source, args.poll, stop_evt)` daemon thread — exactly
  the Channel/Crew pattern.
- Only built when `not args.sheet_csv_url` and not `--no-event-notes` (a custom CSV URL or
  the opt-out disables it, mirroring Channel/Crew).

### Endpoints (read-only GET, identical payload)
Payload shape:
```json
{ "available": true, "notes": [ {"heading": "...", "note": "...", "priority": "info|important"} ] }
```
- **Tailnet:** `GET /event-notes/data` — a new branch in the `do_GET` if-ladder. Returns
  `available:false, notes:[]` when the source is disabled/absent or has no rows.
- **Funnel:** `GET /console/event-notes/data` — a branch inside `_console_gate`, returning
  the same payload helper. Authorization `Requirement(ANY)` (any authenticated console
  subject — Director, Commentator, Race Control all see the same notes). No new public
  surface beyond the existing `/console` mount; feed URLs etc. are untouched.

When `--no-event-notes` is set the source is `None`; both endpoints return
`available:false` (the front-end then hides the button). This matches the
"disabled feature → endpoint degrades, card self-hides" pattern used by the broadcast-chat
card.

### Front-end (three pages: `director-panel.html`, `cockpit.html`, `race-control.html`)
- **Button** in the `.appmeta` header region next to the `? Help` link: `📋 Notes`.
  Hidden (`hidden`) while `available` is false or the list is empty.
- **Modal:** a native `<dialog>` (template = the Control Center `updmodal`): backdrop,
  Esc-to-close, focus-trap for free. Title "Event Notes". Body rendered with `textContent`
  (XSS-safe); a `Heading` row → bold subheading line; an `Important` note → accent chip +
  left border. A close button.
- **Polling:** via the patched `fetch`/`RC_API` shim, call the bare path
  `/event-notes/data` (the shim prepends `/console` under Funnel automatically). Cadence
  30 s, self-rescheduling like `pollSchedule`; a poll that gets no answer (network error,
  8 s abort, non-auth error status) retries after 5 s instead, so a relay hiccup at page
  load does not hide the button for a whole period (#1031). The poll result drives both the
  modal body and the button's visibility.
- The three pages already duplicate the shim/header boilerplate; the notes button + modal +
  poll is the same snippet in each, consistent with the codebase.

## Edge cases & failure modes
- Sheet unreachable / transient fetch error → source keeps its last good rows (or empty);
  endpoint still returns `available` per whether rows exist; front-end never errors out.
- Empty tab / header-only / missing `Note` column → `notes:[]`, `available:false`, button
  hidden.
- `--sheet-csv-url` (synthetic/dev) or `--no-event-notes` → source not built → disabled.
- Tab name with a space (`Event Notes`) → URL-quoted in the gviz URL (already the pattern).

## Testing
- `tests/test_event_notes.py` — pure parser: header location (any column order), empty/
  header-only CSV → `[]`, rows with empty `Note` skipped, priority normalisation
  (`Important`/`important`/`IMPORTANT` → `important`; `Info`/empty/unknown → `info`),
  missing `Priority`/`Heading` columns degrade cleanly.
- Relay endpoint tests (style of `test_broadcast_chat.py` / `test_cockpit.py`): tailnet
  `/event-notes/data` payload, the `/console/event-notes/data` mirror, `available` flag for
  enabled-with-rows vs. disabled/empty.

## Docs & screenshots
- **Director Panel is screenshot-blocking** (CLAUDE.md hard rule): regenerate
  `src/docs/wiki/images/director-panel.png` in the same change via the `wiki-screenshots`
  skill (demo profile + obs-sim; seed a few notes so the button + modal show content).
  Refresh `console-cockpit.png` and `console-race-control.png` as good practice in the same
  PR.
- Document the `Event Notes` tab in the Sheet-side wiki (Sheet-Template / Sheet-Webhook) as
  a league coordination item (header `Heading | Note | Priority`), the way the `Channel` /
  `Crew` / `Cue Preset` tabs/columns are documented.
- No machine `.env` knob — nothing to add to `.env.example` / `Configuration.md` beyond the
  Sheet-side note.

## Out-of-scope follow-ups (not this PR)
- Per-audience targeting (an `Audience` column) if the owner later wants role-specific notes.
- A count badge or change-highlight if "did I see the latest note?" becomes a real need.
