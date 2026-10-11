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
| macOS Codex | 0.162.1 | reused | strict report/history/export passed; outside synthetic reads/writes blocked | unavailable model and cancellation passed |
| macOS Claude Code | 2.1.296 | reused in GUI Terminal | strict report/history/export passed; outside Read refused with approval guidance | unavailable model and cancellation passed |
| Windows Codex | 0.162.1 | reused | strict report/history/export passed with mandatory MXC; outside synthetic reads/writes blocked | unavailable model and cancellation passed |
| Windows Claude Code | 2.1.284 | reused | strict report/history/export passed; outside Read refused with approval guidance | unavailable model and cancellation passed |
| Linux Claude Code | 2.1.296 | reused in the signed-in OS user context | strict report/history/export passed; outside Read refused with approval guidance | unavailable model and cancellation passed |

macOS SSH and the desktop keychain are distinct execution contexts. The GUI
subscription works with the runner's preserved USER identity. On Linux the existing
Claude session belonged to another OS user than the Codex workspace process; the
native verification reused that signed-in user without copying credentials or
reauthenticating. Each application checks the subscription of its own OS user.

The macOS and Windows checks used synthetic frozen packages prepared on the
supported Python toolchain, then ran the real native adapters, runner, strict
validator, profile-owned history and exports. The selected models were
`gpt-6.1-sol` and `haiku`; Claude reported `claude-haiku-5-5` on Linux/macOS and
`claude-haiku-4-5-20251001` on Windows. Codex did not report an actual model name.

Native Windows verification exposed disabled sandbox recognition and missing
runtime environment locations. Windows Codex now explicitly requires MXC and
unified command execution. It inherits only an explicit list of Windows runtime
paths and identity variables, while root-deny file permissions and disabled tool
network access remain active. There is no elevated/unelevated fallback or automatic
administrative setup. Missing MXC is an actionable `isolation_unavailable` failure
with no normal report. A native canary verified that the package remained readable
while outside synthetic reads and writes received access denied.

The independent security review rejected the initial elevated fallback, which
could trigger administrative setup when MXC was unavailable. The final adapter
selects MXC directly. See the [official Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox).

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

Eighteen guards were falsified individually from the committed implementation:
selection profile binding, positive reference IDs, selected-only shift replay,
stale enrichment, backend request-profile binding, delayed preview generation,
settings refresh invalidation, response-profile binding, comparison identity,
completion generation, completion job identity, initialized-profile requests,
MXC-only backend selection, unified Windows execution, Windows runtime propagation,
command environment filtering, unavailable-MXC guidance and Claude unknown-model
diagnostics.
Each failed its intended assertion before the committed file was restored.

The final native checks covered all six platform/provider combinations, including
strict reports, exports, explicit unavailable-model failures and cancellation.
All inputs were synthetic; no credentials or private recordings entered CI.

Self-review reproduced a completed job disappearing from the UI when a future
goal was edited during its response. The watcher now keeps that job until its
completion is displayed. An older completion response also cannot clear a newer
job watcher. Both delayed-response browser cases have red/green regressions.

CI exposed a fast-startup race: opening Telemetry before its profile loaded sent
profile=null status/history requests. The backend correctly rejected them with 409.
The client now defers profile-related requests until that profile is known and
loads history/status when initialization finishes. A browser request listener
reproduced the null-profile requests before the fix; all 20 visual surfaces then
passed without suppressions or timeout changes.
