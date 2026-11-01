# Control Center operator actions (#1048)

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Follow the existing Install/Update workflow | Existing `ui_ops`, `/api/op/`, `JobManager` and output console | OBS benchmark, smoke and Linux installation actions become ordinary jobs | Invalid numbers, unknown parameters and stale profile rejected before spawn | Real HTTP and native browser clicks with captured jobs |
| Preserve the smoke confirmation | Existing prompt UI and CLI `_smoke_confirm` | The operator's actual phrase reaches the unchanged CLI through finite stdin | Missing/wrong/oversized phrase never starts a job; actual CLI rejects wrong input | Harmless real child executes only the confirmation guard |
| Transfer crew chat and health history | Relay controls and allowlisted jobs | Pull/import/export argv with selected host/file | Leading options, control characters, invalid port or changed profile rejected | HTTP status, business error and frozen argv |
| Record GT7 telemetry | Telemetry controls and existing record CLI | Start/stop/status use the broadcast profile | Missing/foreign profile or invalid parameters cannot spawn | Actual UI clicks and captured child argv |
| Reach remaining public actions | Existing callbacks and added service/Console controls | Stream/Companion restart, relay pages, Companion pages and Funnel actions | No free-form operation or argv input | Rendered controls and captured jobs |
| Enforce future CLI/UI parity | CLI grammar/verb inventory and audited existing bindings | All public actions have a reachable binding | Injected new root, service or nested action without a binding fails | Executed guard regressions and isolated falsification |
| Preserve the localhost boundary | Global CSRF guard | Same-origin browser and originless CLI clients retain access | Opaque/malformed/non-HTTP Origin or Referer denied before any callback | Real `text/plain` JSON POST with `Origin: null` launches nothing |

## Existing workflow

The established Install/Update actions use allowlisted jobs, confirmation dialogs
and the Control Center console. The new actions use that same workflow. Linux
installers retain their existing authorization requirements and diagnostic output.
No terminal launcher, new privilege broker or private CLI entrypoint is added.

The smoke action asks for `CLEAR SCHEDULE <profile>` using the existing prompt
pattern. The server checks the selected profile and phrase before admitting a
job. Only this operation accepts finite stdin, containing the operator's actual
input. A temporary file passes it to the unchanged CLI confirmation; no automatic
yes, force flag or confirmation bypass is supplied. The CLI retains its live-event
checks. Force cancellation is unavailable for smoke because terminating its
process would skip its existing event teardown. Stop UI refuses shutdown while
smoke runs, even with an AI interruption acknowledgement. Admission closes under
the job lock when shutdown is accepted, so a concurrent smoke request cannot
slip past the check. Smoke owns a non-daemon output reader: natural main exit or
Ctrl+C cannot close its output pipe before the child finishes its cleanup.
Its output reader is admitted before the confirmed child starts; exhausted thread
capacity rejects the action without spawning. A failed process launch releases
the waiting reader instead of stranding a non-daemon thread.

The independent review found that the existing global CSRF guard accepted
`Origin: null`. It now rejects explicit opaque, blank, malformed and non-HTTP
origins/referers before privileged dispatch. Missing Origin and Referer retain
non-browser compatibility. The HTTP regression sends simple `text/plain` JSON
to the smoke and Funnel operations and proves HTTP 403 with zero job callbacks.

## Verification

`tools/e2e-operator-actions.py` drives the shipped UI through real loopback HTTP
with fake jobs, devices, providers and services. It covers confirmation refusal,
actual typed smoke input, bounded argv, profile conflicts and the newly exposed
controls. It captures the matching wiki screenshots from an unstamped dev UI.
No real OBS, streams, Sheet writes or installation actions run during checks.
The browser test is part of the existing Linux visual and native Mac/Windows CI.
Its local Linux entry uses the repository's fail-closed cgroup admission and
finite runtime lease. Native Mac verification also uses bounded CPU, memory,
process and runtime budgets and checks owned-process cleanup.
