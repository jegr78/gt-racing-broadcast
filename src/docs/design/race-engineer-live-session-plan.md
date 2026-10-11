# Race Engineer live-session implementation plan

Scope: parent #850 and its six native sub-issues. The tracker is authoritative;
this checklist records implementation and verification, not a second issue list.
Each issue receives its own branch and PR, reviewed and squash-merged after green CI.
The standing release PR is outside this work.

## Sequence and delivery checklist

- [x] Read parent/children, current main, open PRs, worktrees and remote branches.
- [x] #851: reproduce a growing provisional lap with stepped production packets;
  return the latest completed authorized comparison while refreshing it; stabilize
  map bounds and chart distance against a complete outline. Use the replay fixture
  while developing the reusable tool in #856.
- [ ] #856: package recording replay as a maintainer tool with temporary copied
  sources, signed engineer access, paced/accelerated playback, bounded pre-feed,
  explicit data update and offline fallback; run synthetic checks in CI.
- [ ] #852: show car and granted reference names, reset manual new-lap notices,
  and draw the current snapshot's car independently from selected lap traces.
- [ ] #854: parse validated race position/car count, publish instruments and
  lap-line position, and display position on the desk.
- [ ] #853: implement all eight display cleanup items as planned; record any deferred
  items explicitly.
- [x] #855: render layout drafts at 1440, 1024 and 390 px and link them in the issue.
- [x] #855: obtain the expressly required producer approval of a concrete draft.
  Proposal A at `d0131a9` in #859 was explicitly approved in the working chat and
  recorded in #855.
- [ ] #855: implement the approved draft and verify all three viewports.
- [ ] Verify all children and parent scope, close #850, reconcile tracker and
  remove only this run's merged topic branches.

## Requirements and evidence matrix

| Issue | Production path | Positive test | Failure-path test | Runtime evidence |
| --- | --- | --- | --- | --- |
| #851 | Comparison worker/cache, desk map/chart | Growing provisional pair yields delta/sectors; newer result replaces old | Revoked grants, changed context/generation, unavailable outlines, bounded work | Stepped packets through real HTTP and Chromium |
| #856 | Maintainer replay, recording reader, production Store/handler | Snapshot advances, lap confirms, provisional comparison becomes usable | Bad input/options, source copies remain private, offline data, queue/lifecycle cleanup | Synthetic CI replay and optional private recording supplied by path |
| #852 | History projection and desk state | Car/reference names, notice reset, live marker | Missing names, safe label rendering, missing position, manual selection retained | Browser assertions and refreshed desk screenshots |
| #854 | Packet parser, live instruments, lap summary, desk | Valid Pn/count and line position | Negative/zero/out-of-range/count edge cases | Synthetic packets plus browser display |
| #853 | Desk context/placeholders/charts/map/sectors | Agreed readable displays | Missing data distinct from undriven data, empty chart, label collisions | Browser assertions and screenshots |
| #855 | Responsive desk layout | Approved design at all three widths | Overflow, unavailable comparison, phone selection feedback | Rendered approval drafts then matching production screenshots |

## Per-issue completion checklist

1. Re-read issue and current main; exclude parallel ownership.
2. Extend the requirement matrix; observe the intended failing regression.
3. Implement, run focused tests, document behavior and refresh changed UI images.
4. Commit correct code; falsify each new guard individually and restore it.
5. Review code and use an independent read-only security pass; fix confirmed findings.
6. Run complete tests, lint and distributable build on the final committed head.
7. Open the conventional PR, monitor all cross-platform CI and merge exact green head.
8. Verify issue/parent/project state, publish and browser-check changed wiki pages,
   then clean the proven merged branch and update this checklist in the next change.

## Decisions and compatibility

- #855 requires draft approval before production layout changes. Prepare that
  reviewable artifact while implementing independent work; approval is not inferred.
- #856 mentions Python 3.10+, but the current source baseline is Python 3.12+
  following #847. The replay must use the supported production baseline.
- Recording paths and private captures remain outside Git and CI. Downloads must
  be explicit, consistent with #847; offline replay uses existing/bundled data.
- No cloud provisioning, OBS control, relay start or UDP listener is needed.
