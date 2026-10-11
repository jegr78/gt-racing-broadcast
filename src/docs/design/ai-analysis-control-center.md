# Optional AI Control Center workflow

Requirement matrix for #821. Existing telemetry works independently of provider tooling.

| Criterion | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Machine opt-in/configurations | settings card + Settings | CRUD/check/manual model/timeout/mode | disabled/missing CLI/login/model, unsupported extensions | browser settings and native probes |
| Completed sessions/laps + references | Controller selection/reference APIs | completed usable/excluded metadata, unchecked suggestions | incomplete/foreign/invalid selection | real browser selection |
| Explicit preview/start | dedicated UI card + bound confirmation | notes/limits/transmission and explicit start | changed selection/profile/model invalidates preview, oversized package | browser preview and idle invocation count |
| One active job/cancel/retry | Runner callbacks + UI poll | progress, cancellation, deliberate new preview | provider/timeout/validation failure, foreign-profile redaction | browser controlled process and native lifecycle |
| Validated reports/history | Store + text-only DOM | exact lap links, three exercises, stale/language/export | script text, incomplete body, switched profile | real browser report/export/navigation |
| Selected optional shift facts | package source enrichment + stale reconstruction | selected offline shift/reference snapshot | unavailable shifts, unrelated laps not replayed | synthetic package and historical reconstruction |
| Documentation/screenshots | operator wiki and same-change images | opt-in/install/login/data flow/manual guidance | no quota guarantee or implicit billing/install | inspected dev-build screenshots and published wiki |
| Real native subscriptions | provider adapters on three OSes | login reuse, model, confinement, structured result | missing model/approval/cancel and no normal failed report | native Codex/Claude Windows/macOS/Linux, versions recorded |

## Native subscription evidence

Release acceptance requires real authenticated provider invocations on all three
operating systems. Controlled CI process fixtures do not replace this requirement.
`python tools/verify-ai-cli.py --provider PROVIDER --model MODEL` uses synthetic
recordings and the actual package/controller/runner/validator/store pipeline. It
retains local diagnostics, verifies structured reports/exports, deliberately tries
an unavailable model and cancels a new process tree. It never signs in, installs a
CLI or supplies provider credentials. Pass `--executable` for an existing binary.
The command can consume subscription quota and is a maintainer check, not CI.

| Platform/provider | Observed CLI version | Existing subscription | Real result/confinement | Native failure/cancel |
| --- | --- | --- | --- | --- |
| Linux Codex | 0.162.1 | reused | full strict report/history/export passed; outside synthetic reads/writes blocked | unavailable model and cancellation passed |
| macOS Codex | 0.162.1 | reused | structured package probe passed; outside synthetic reads/writes blocked | controlled native process fixtures passed; real provider lifecycle pending |
| macOS Claude Code | 2.1.296 | reused in GUI Terminal | structured package probe passed; outside Read refused with approval guidance | real provider cancellation pending |
| Windows Codex | 0.162.1 | authentication probe passed | real model run pending | controlled Windows CI process-tree checks passed; real provider lifecycle pending |
| Windows Claude Code | 2.1.284 | authentication probe passed | real model run pending | real provider lifecycle pending |
| Linux Claude Code | 2.1.296 | refresh failed; current CLI status reports no login | real model run pending | real provider lifecycle pending |

macOS SSH and the desktop keychain are distinct execution contexts. The confirmed
GUI subscription works with the runner's preserved USER identity; no credentials
were copied. Subsequent Mac/Windows SSH connectivity became unavailable. The Linux
Claude CLI reports an OAuth refresh failure and then no current CLI login; this does
not establish the user's login state in another Claude application. These pending
native checks keep #821 and its parent open. Do not describe this matrix as complete
or release-ready until the remaining actual integrations have passed.

## Review and browser evidence

The independent security review reproduced two races. A settings refresh could
change the visible model while retaining a preview of another model; it now preserves
manual selection and invalidates the preview. A server profile change could precede
the UI update and expose another profile's history/report; every profile-related
request binds the visible profile, the backend refuses mismatches and the client
checks the response/current profile. Both have browser regressions and clean re-review.

The real browser checks configuration CRUD, CLI/model suggestions, enable/disable,
completed lap selection and unchecked references, previews with no inference,
selection and delayed-response invalidation, validated text-only reports, escaped
HTML download, exact lap and comparison navigation, validation failure, cancellation
and explicit/external profile switching. Ordinary telemetry uses the existing live
callbacks while the provider is a controlled Python process. All data is synthetic.

Nine new guards were falsified individually from the committed implementation:
selection profile binding, positive reference IDs, selected-only shift replay,
stale enrichment, backend request-profile binding, delayed preview generation,
settings refresh invalidation, response-profile binding and comparison identity.
Each failed its intended assertion before the committed file was restored.

The final maintainer verifier repeated the complete native Linux Codex pipeline
on the UI implementation with model gpt-6.1-sol: validated report/history/exports,
unavailable-model failure and cancellation all passed. A separate direct restricted,
tools-disabled Linux Claude invocation independently returned "OAuth session expired
and could not be refreshed", with zero API duration and zero token usage.
