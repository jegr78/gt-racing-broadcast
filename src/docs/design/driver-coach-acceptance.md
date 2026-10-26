# Integrated coaching acceptance

The coaching path is delivered in order: explicit calibration, measured assessment,
one retained focus, shared native scheduling, driver controls, private history,
then this integrated acceptance gate. Telemetry and driver-review mathematics from
the existing Engineer/AI foundations remain the measurement source. This work does
not reopen those completed epics or replace measured evidence with generated advice.

| Requirement | Production path | Positive evidence | Failure evidence | Integrated runtime gate |
|---|---|---|---|---|
| Fresh personal calibration | Calibration + relay start watermark | Comparable completed laps establish a baseline | Prior history and a partly driven start lap cannot count | Signed driver controls and fresh-session browser replay |
| Stable, repeating and isolated differences | Assessment findings and evidence IDs | Repeated measured technique differences support one exercise | Isolated differences and missing channels cannot invent a technique | Assessment and focus production replay suites |
| Consistent but nonoptimal self-reference | Personal baseline versus working reference | Starting measurements remain fixed when references change | A slower reference cannot redefine improvement | Calibration/focus reference promotion regressions |
| Connected corners | Passage plus following section and exit checks | Net measured progress includes downstream outcome | A local gain with downstream loss is not a successful exercise | Real assessment/section replay |
| One sustained focus | Focus retained across comparable attempts | Repeated progress, skip/keep and skill constraints | Unsuitable laps do not add an attempt or silently replace a constraint | Native controls and browser interactions |
| Context and archive rights | Coordinator epochs and separate grants | Current compatible reference may support a focus | Changed car/track/definition, corrected source and revoked grant remove unproven details | Signed HTTP final-IO and history-grant regressions |
| Bounded live work | Four detail reads per step, bounded cohorts and source ledger | New ready laps continue after pending/evicted detail | Old cursors, expired speech and blocked IO cannot revive output | Long-session, workload and cancellation regressions |
| Shared native owner | Existing capture/playback Supervisor and Ticket lane | English coaching shares output with explicit requests | Urgent Engineer output wins; Stop cancels stale work | Real Supervisor with fake worker/player, OS process tests |
| Narrow driver controls | Verified assignment and fixed local producer route | Explicit start/stop/objective/skill/reference | Crew or Engineer role alone does not grant driver access | Source and frozen relay HTTP acceptance |
| No coaching scores on replay | Live receipt and replay exclusion | Replay can be viewed while training waits for live evidence | Playback never manufactures a personal baseline or scored attempt | Actual relay replay acceptance in source and frozen package |
| Private progress | Compact owner/profile journal and current debrief | End summary survives without traces or recordings | Deleted training, interrupted writes and old identities cannot revive data | Journal, HTTP and temporal browser acceptance |
| Safe next-session reuse | Explicit suggestion after fresh calibration | Compatible prior skill can be applied by the driver | Historical times, grants or arbitrary narrative never become current facts | Fresh-session browser acceptance |
| Optional AI | Explicit training consent and isolated profile agent | Bounded current scalar facts and positive response templates | No provider fallback, raw history upload or invented driving action | Provider schema and injection regressions |
| Packaged operation | Same relay, controls and journal in the distributable | Packaged relay serves private controls and writes a compact checkpoint | Missing bundled modules and wrong frozen paths fail the gate | Actual PyInstaller relay acceptance |

The matrix is backed by these executable suites, which use production modules:

- `tests/test_driver_coach_calibration.py`: fresh receipt, comparable cohorts,
  corrected eligibility, owner/context isolation and retained reference consent.
- `tests/test_driver_coach_assessment.py` and `tests/test_driver_coach_focus.py`:
  recurring versus isolated differences, connected sections, absent channels,
  nonoptimal references, fixed personal baselines and repeated measured progress.
- `tests/test_driver_coach_service.py` and `tests/test_driver_coach_controls.py`:
  bounded worker IO, native priority/cancellation, consent, explicit controls,
  source correction and replay/stale reference exclusion.
- `tests/test_driver_coach_history.py` and `tests/test_driver_coach_http.py`:
  journal limits, restart/deletion, private namespaces and authorization/source
  checks after slow IO. The actual Control Center client is included.
- `tools/coach_browser_check.py`: all three real browser surfaces, fresh-session
  reuse, drafts, deletion, identity changes and delayed responses with equal actor
  revisions after a context change.
- `tests/test_e2e_coach.py` and `tools/e2e_coach.py`: the actual signed relay HTTP
  control/journal path; refusal of unowned transports and incompatible runtime modes.

Run `python tools/run-tests.py`, `python tools/lint.py` and `python tools/build.py`
for the canonical local gate. `python tools/e2e.py --visual` adds the full browser
acceptance suite. `python tools/e2e.py` and `python tools/e2e.py --build` exercise
the source and Linux frozen integration path, including coaching. For the portable
Solo gate, run `python tools/e2e.py --coach-only` or build the actual native package
with `python tools/e2e.py --build --coach-only`. The Windows/macOS `native-package`
CI jobs use the latter command without POSIX stubs. Package checks must finish green
before enabling Auto-Merge, even when branch protection does not require those jobs.

The source and frozen integration runs use owned temporary profiles, synthetic
GT7 recordings, a local OBS stand-in and explicit driver assignment. Voice and
speech providers stay disabled. They do not access a live microphone, paid service
or personal recording. A valid Engineer token alone must receive no driver access.

The final evidence records the exact head and current main base, canonical full
test/lint/build results, real browser views, independent security review, individually
falsified trust guards and actual CodeQL results. macOS, Windows and Linux validation
must name the systems or CI runners that ran it. An unavailable personal machine is
never represented as a completed personal-machine check.

Readiness means all seven children of #977 are implemented, their PRs actually
merged after green verification, and the parent scope and tracker reconciled. An
enabled Auto-Merge setting or a written plan does not meet that gate.
