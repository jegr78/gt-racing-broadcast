# Director Panel shell (#728)

Spec: `docs/superpowers/specs/2026-10-04-director-panel-workflow-redesign-design.md`,
increment 1. The page keeps its JS; the markup and CSS become a fixed frame.

## Rule that makes it safe

Every existing card stays in the DOM. Area containers only toggle visibility, so the
pollers keep writing by id whatever area is showing. Nothing is rendered conditionally.

## Steps (one commit each)

1. Frame markup: header (one row), banners, nav, live column, workspace with one
   container per area, chat rail. Existing cards move into their area unchanged.
2. Chats become sections (a `<details>` does not pass a height to its children in
   Chromium); the two `.open` checks go through `chatVisible()`.
3. Nav JS: area switch, remembered per browser (`rc_area`, `rc_nav_collapsed`), state
   dots and one-line reasons from `/status`.
4. Live column: ARM for the off-air feed, NEXT (moved out of the feeds bus), "next
   step" strip, Handover area from `/schedule/data` and `/submissions`.
5. Solo: Handover hidden, Graphics is the default area, the scene bus moves into the
   live column (until #729 turns scenes into macros).
6. Breakpoints: < 1600 nav collapses, < 1280 chat drawer, < 900 single column.
7. Tests: structure tests rewritten, parity guard against the ids of the old page.
8. `Director.md` and the two panel screenshots.

## Out of scope (later sub-issues)

Macro consolidation (#729), relay data and NEXT transition (#730), keyboard
shortcuts (#731). Solo tiles for capture, webcam and tyres need a preview endpoint
that does not exist yet (#730).
