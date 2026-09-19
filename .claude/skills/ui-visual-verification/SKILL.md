---
name: ui-visual-verification
description: Rigorously verify a UI change by actually RENDERING it and looking at the result, before marking a fix/feature done. Use whenever you edit a rendered surface — the Director Panel (src/director), Commentator Cockpit (src/cockpit), Crew Console (src/console), Race Control desk (src/racecontrol), the relay overlay pages (src/obs/hud.html, splitscreen.html), the Control Center (src/ui, src/racecast_ui.py), or a per-league overlay CSS (profiles/*/overlay). The blocking Stop hook ui_visual_verify_gate.py requires this skill's marker step before completion. NOT a substitute for wiki-screenshots (that regenerates the committed wiki image); this is your own look-at-it check that catches styling/alignment bugs first.
---

# UI visual verification

**Why this exists.** Styling and alignment bugs ship because a UI change is reasoned
about but never *rendered and looked at* — e.g. issue #397: the Director Panel duration
input was left as an unstyled white browser box on a dark theme, and it was marked done
without anyone viewing it. Reading the diff is not verification. **Looking at the rendered
pixels is.** This skill is the disciplined look, and the `ui-visual-verify-gate` Stop hook
blocks completion until you record that you did it.

Sibling: [wiki-screenshots](../wiki-screenshots/SKILL.md) regenerates the *committed* wiki
image (a separate, required step for Control Center / Director Panel / Companion). This
skill is your own **pre-flight look** — do it first; it usually reuses the same running
demo build.

## When it applies

Any edit to a rendered surface. The Stop hook watches exactly these paths:

| Surface | Files |
|---|---|
| Director Panel | `src/director/*.html` |
| Commentator Cockpit | `src/cockpit/*.html` |
| Crew Console (launcher/pages) | `src/console/*.html` |
| Race Control desk | `src/racecontrol/*.html` |
| Relay overlay pages | `src/obs/hud.html`, `src/obs/splitscreen.html` |
| Control Center | `src/ui/*.{html,css,js}`, `src/racecast_ui.py` |
| Per-league overlay CSS | `profiles/*/overlay/*.css` |

A pure-logic/comment edit to one of these files still counts as "changed" — you either
render it or explicitly acknowledge it needs no render (both satisfy the gate via the
marker step below).

## The procedure

### 1. Serve the surface
- **Relay-served** (`/panel`, `/cockpit`, `/console/*`, `/hud`, `/splitscreen`): boot the
  demo relay + obs-sim exactly as in [wiki-screenshots](../wiki-screenshots/SKILL.md)
  Part B (demo profile, stub cookies, `tools/obs-sim.py`, `RACECAST_OBS_WS_*` → the sim).
  The HTML is read per request, so a browser reload picks up an edit with no relay
  restart.
- **Control Center** (`cc-*` views): `RACECAST_UI_PORT=<free> racecast ui --no-browser`
  from `src/` (never port 8089 — that's the operator's real instance; scan 8090+).
- **Quick CSS-only check** with no data: serve `src/` statically
  (`python3 -m http.server <port>` from `src/`) and open the page — file:// is blocked in
  the Playwright MCP, and the static server is enough to judge pure styling. The JS polls
  will error against no relay; the static markup still renders.

### 2. Screenshot the CHANGED component — element, real size
Use the Playwright MCP. Take an **element** screenshot of the specific thing you changed
(e.g. `#txBar` for the transition row), not just a full-page grab — real size, no scaling,
so you judge the actual pixels. Set a realistic viewport width first.

### 3. Read the PNG back and actually look
`Read` the screenshot into context and check, deliberately:
- **Theme fit** — does it use the surface's CSS variables (`--edge`, `--ink`, `--panel`,
  `--mono`, …)? A default browser control (white `<input>`/`<select>`, blue focus ring) on
  a dark panel is the #397 smell.
- **Consistency** — does it match sibling components? (e.g. the cue-compose inputs are the
  reference for panel inputs.) Same radius, border, padding, font.
- **Alignment & spacing** — vertically centered within its row? consistent gaps? not
  clipped or overflowing?
- **Interaction states** — check `:focus` and `:disabled` (and `:hover` where it matters).
  For the duration field, CUT disables it — verify the disabled style reads as disabled.
- **Against the intent** — compare to the issue screenshot / design ask. Does it fix what
  was reported?

If anything is off, fix and re-shoot. Do not proceed on "the CSS looks right in the diff".

### 4. Refresh the wiki screenshot (same change)
For Control Center / Director Panel (CLAUDE.md hard rule) regenerate the committed image
via [wiki-screenshots](../wiki-screenshots/SKILL.md) and commit it alongside the code.
Cockpit / Race Control: good practice, not a release blocker.

### 5. Record the verification marker (satisfies the Stop hook)
The gate compares each changed UI file's **current content hash** against
`runtime/ui-visual-verified.json`. After you have genuinely looked (step 3) and it's
correct, record the hashes:

```bash
python3 .claude/hooks/record_ui_verified.py src/director/director-panel.html [more files…]
```

Run it with **every** changed UI file the gate listed (including any you judged
non-visual). Re-editing a file changes its hash and re-arms the gate, so record **after**
your final edit. The marker lives under `runtime/` (gitignored) — it is local session
state, never committed.

## Cleanup
Tear down the demo build as in [wiki-screenshots](../wiki-screenshots/SKILL.md): `relay
stop`, `pkill -f obs-sim.py`, `rm -f runtime/demo/stub-cookies.txt` (the shared jar stays), and
`git checkout -- profiles/demo/profile.env` (the auto-provisioned `CONSOLE_SECRET`). Delete
any scratch PNGs from the repo root; only files under `src/docs/...` are committed.

## Notes
- The marker is trust-based — it records that YOU looked, it can't prove it. Recording it
  without doing step 3 defeats the entire point (and reintroduces #397). Don't.
- The gate only blocks for surfaces changed **on this branch/working tree**, and only until
  recorded. Non-UI branches never see it.
