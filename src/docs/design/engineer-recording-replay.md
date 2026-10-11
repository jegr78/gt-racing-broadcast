# Maintainer Engineer recording replay

Requirement matrix for #856, under #850. This tool serves the production Engineer
desk on loopback without starting relay feeds, OBS, UDP or changing host settings.
It is separate from the operator replay feature in #857.

| Requirement | Production path | Positive proof | Failure proof | Runtime proof |
| --- | --- | --- | --- | --- |
| Private temporary copied sources | ReplaySession setup and access Store | Copied live/reference recordings and labelled grants | Invalid recording, label mismatch, no source mutation, temporary cleanup after failure | Real recording files and signed HTTP |
| Production desk and bounded live Store | make_handler, actual page, shared API | Snapshot/history/lap/compare responses | Unsigned request rejected, unrelated operational routes unavailable | Ephemeral loopback server |
| Real data with deliberate updates | CarDB, TrackDB and gt7_data | Reuse existing cache and bundled offline defaults | No default network request, invalid optional cache falls back | Normal replay and synthetic learned circuit |
| Recorded timing and bounded pre-feed | Playback clock/deadlines and packet batches | Fake-clock pace, speed factor and skip; offline lap equality | Invalid finite options, no queue drops, cancellation interrupts waits | Accelerated real packet playback |
| Moving-lap CI proof | Synthetic recording and HTTP check callback | Confirmed laps plus available delta/sectors while current lap keeps growing | Each growing step must keep the prior result; no static-only check | --check in CI e2e |
| Owned lifecycle and screenshots | Context manager, playback/server/Store teardown | Desk remains available after EOF until stopped | Cleanup on setup failure, interruption and playback error | CLI and reusable screenshot fixture |

Source execution uses the repository's supported Python 3.12+ baseline. The
original issue's 3.10+ wording predates #847. Private recordings never enter Git
or CI; all tool inputs are explicit local paths. Reference downloads require
`--update-data`; the default remains offline.

CLI proposal: `python3 tools/engineer-replay.py RECORDING [--reference FILE]
[--reference-label LABEL] [--speed X] [--skip-minutes MIN] [--port PORT]
[--data-runtime DIR] [--update-data]`. `--check` creates synthetic input and exits
after checking the actual HTTP analysis flow. Normal playback keeps the desk
available after EOF until Ctrl+C; packet receipt then naturally becomes stale.
