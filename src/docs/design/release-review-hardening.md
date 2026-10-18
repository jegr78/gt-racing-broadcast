# Release review follow-ups

Issue #847. Producer decisions: GT7 downloads are opt-in; AWS/GCP provisioning
is retired and replaced with a short Linux applications/libraries overview.
Readability enforcement targets AI/Race Engineer modules rather than reformatting
unrelated legacy code. Source execution requires Python 3.12, the lowest version
in the current CI matrix; packaged binaries include their interpreter.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| No implicit GT7 download | Relay watcher and environment default | Explicit opt-in downloads; manual files reload | Unset setting never calls update | Injected update spy and actual watcher loop |
| Reject CLI flag-shaped settings | ai_agents config and invocation | Executable/model normal values retained | Leading dash rejected before probe/inference | Settings and invocation unit paths |
| Clear source Python floor | Early CLI guard and preflight | Supported interpreter proceeds | Older interpreter gets actionable message before imports | Simulated version subprocess and classifier |
| Readable AI/Engineer Python | Scoped Ruff rules and formatted modules | Existing behavior unchanged | Reintroduced semicolon/spacing violations fail lint | AST equivalence before/after format and lint fixtures |
| Retire cloud provisioning | Maintainer files, tests and wiki links | Linux dependency overview and valid links | No active provisioning entrypoints or provider defaults | Repository search and wiki link/build checks |

The former definition/context-cache blocker is completed in #846 / #848.
No provisioning, reboot or cloud-account operation is required after retirement.

## v1.13.0 self-review corrections

The release review selected these five follow-ups. The behavioral regressions live
in `tests/test_release_review_fixes.py` and use disposable recordings, loopback
HTTP servers and synthetic provider processes without subscription credentials.

| Finding | Required behavior | Verification |
| --- | --- | --- |
| R1, telemetry mutation origins | Delete and track assignment reject opaque/foreign origins even without a profile query; intended local clients still work | HTTP requests with `Origin: null`, a named foreign origin, same origin and no Origin |
| R2, UI-owned AI lifecycle | Confirmed UI shutdown and Ctrl+C cancel and join owned workers before exit; new starts are rejected during shutdown; external CLI admission stays untouched | Real UI subprocess, provider and child exit, retained cancelled artifact, lease held through paused tree cleanup |
| R3, cancellation ownership | A foreign analysis profile cannot cancel an active job; its owning profile can | Two-profile HTTP status/cancel requests and cancellation-file inspection |
| R4, recording replacement | Disk and memory indexes reject replacement with preserved size/mtime; resumed reads retain the same file and header identity | Public lap API with warm/disk indexes, atomic replacement, and in-place UUID rewrite with append |
| R5, shared definitions | Archive annotations detect equal-size/equal-mtime replacement of local overrides and bundled definitions | Warm public lap responses acquire the replacement's fingerprint |

Index version 10 invalidates older lap caches. File signatures include device,
inode, size, nanosecond mtime and change time. Windows reads `FILE_BASIC_INFO.ChangeTime`
because Python 3.12's `st_ctime` represents creation time there. A growing recording may resume only on
the same file with an unchanged complete header and valid existing tail check.
Definitions use the same replacement-sensitive signature in archive analysis.

The UI process tracks its background workers separately from machine-wide job
metadata. Shutdown closes admission to that process's background registry, signals
its workers and waits for their normal process-tree cleanup and lease release.
It does not cancel a separate foreground CLI job or stop broadcast services.

The Windows signature uses Microsoft's documented
[`FILE_BASIC_INFO.ChangeTime`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_basic_info)
through `GetFileInformationByHandleEx`. Native-query failures propagate rather
than silently reverting to a creation-time signature. A native Windows regression
checks timestamp-preserving writes; modeled metadata and error/handle-cleanup
tests run on every platform.

## Further v1.13.0 analysis corrections

The subsequent release review selected three more corrections. Regressions in
`tests/test_release_analysis_fixes.py` exercise recorded packets, the live/HUD
consumers and the public Engineer service without private captures.

| Finding | Required behavior | Verification |
| --- | --- | --- |
| R1, equal consecutive lap times | Preserve tied reported times in archive, live preparation and HUD, without accepting the previous time at the line | Equal-time packet recording, delayed publication, stale/different times, incomplete captures and changed lap/car/state |
| R2, recovered context | Recovered references use the same service-boundary and warmup rules as archive analysis | Public reference recovery after summary eviction, with a timed stint boundary |
| R3, theoretical time retention | Evicting full traces does not discard eligible historical mini-sector times | Default 16-detail eviction and a separate multi-lap sector composition below the actual best lap |

An unchanged `last_ms` field alone does not identify a new official time. A tie
waits for the full existing three-second publication window, requires a clean
complete capture and a packet from the next lap in the same car, on track and
neither paused nor loading. The reported duration must fit the receiver-clock
lap duration within two observed packet intervals plus millisecond rounding.
This conservative boundary check can leave a timing-ambiguous tie unconfirmed;
it does not independently measure simulation time. Changed values arriving after
the publication deadline remain unavailable. Index version 11 rebuilds old lap
indexes and carries the observed packet interval through incremental resume.

Reference recovery retains only the recorded start/end/completeness of explicitly
declared stint boundary laps, independently of its bounded summary/detail caches.
Live summaries retain mini-sector times alongside lap length; best and theoretical
figures use the same eligible, context-compatible summary pool. Public thin
responses continue to omit sector arrays and traces.
