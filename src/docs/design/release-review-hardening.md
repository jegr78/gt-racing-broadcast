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
