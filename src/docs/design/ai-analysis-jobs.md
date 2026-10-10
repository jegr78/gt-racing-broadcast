# AI analysis job requirements

Requirement matrix for #819. Execution binds a frozen package/configuration and
never changes provider/model or starts a repair invocation.

| Criterion | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Machine-wide admission | OS-released lease | one CLI/UI run admitted | second process refused | controlled subprocess contender |
| Profile/source/package identity | immutable run metadata | profile switch leaves destination fixed | foreign artifact access | separate profile roots |
| Lifecycle/progress | runner/status | queued, running, handoff/completed | missing CLI/auth/model/provider/output | real fake CLI stdout/stderr |
| Timeout/cancel tree | owned process group | clean finish | cancellation/timeout kills child tree | child sentinel and locked admission |
| One invocation, bounded output | runner | one spawn | permission need, oversized output | invocation count and diagnostics |
| CLI and HTTP | racecast callbacks, origin guard | preview and explicit confirmed start | stale preview, disabled feature, foreign origins | response status plus semantic error form |

The runner hands structured output to a validation callback. Without one it
retains output as awaiting validation, never as a normal completed report.


CLI preview example: `racecast telemetry analyze recording --session 1 --laps
2,3 --agent my-agent --model explicit-model`. Inspect the returned manifest and
cloud-transmission notice. Repeat the selection with `--start --confirm-preview
FINGERPRINT` to start once. `--status` shows admission/progress; `--cancel JOB_ID`
requests owned-tree cancellation. Ctrl+C in a foreground run also saves a
cancelled artifact. Timeout/cancellation cannot recover subscription quota.

Same-origin HTTP: GET `/api/ai/settings`, `/probe?id=...`, `/status`, `/job?id=...`;
POST `/api/ai/settings`, `/preview`, `/start`, `/cancel`. POST start includes the
reviewed `confirm_preview`, profile and exact selection. Semantic errors use
`{ok:false,error:{code,message}}`; busy/stale preview returns 409, foreign origins
403, size limit 413 and profile-scoped missing jobs 404. No route starts analysis
as a consequence of recording completion.

Failed runs retain `manual-invocation.json` with the same restricted argv,
package-local working directory, prompt path and an environment allowlist.
Create its output directory and deliberately run that argv externally without
removing sandbox flags. This is a separate billable subscription attempt, never
an automatic repair. Provider authentication is managed outside Racecast.

Independent review found and corrected descendant survival after normal parent
exit, Windows execution before Job Object assignment and repeated POSIX group
termination. Controlled tests cover all three. Unvalidated output remains
`awaiting_validation`; missing/invalid output never completes a report.


Guard falsification on the committed implementation: individually disabled
machine admission, package file allowlist, snapshot fingerprint, preview binding,
opt-in, profile-source binding, diagnostic bound, output-link protection,
same-origin enforcement and descendant termination. Every mutation failed the
intended behavioral assertion; production files were restored from the commit.
The preview mutation exposed a test cleanup race and the snapshot mutation an
unguarded error lookup; the tests now assert the intended failed state directly.

Controlled subprocess tests passed on native Linux and macOS. The macOS run
also caught a Darwin zombie-only process-group signalling error: the runner now
reaps an exited leader before signalling remaining descendants. No real provider
inference or private recording was used for these fixtures. Windows native proof
and all real subscription integrations are recorded with the release workflow.


Real subscription probes exposed two installation/environment cases: Codex's npm
native executable needs read permission for that exact installed file inside its
own sandbox; no installation parent directory is permitted. Claude on macOS
needs `USER` to find its existing keychain login. The runner preserves OS user
identity and still removes provider/API and Racecast secrets. A Mac SSH session
cannot read GUI keychain credentials, so native Claude tests ran in the user's
local Terminal session without copying credentials. Codex 0.162.1 on Linux/macOS
read a synthetic package value exactly while outside reads/writes stayed blocked.
Claude 2.1.296 on macOS returned structured output with reported model
`claude-haiku-5-5`; an outside-read attempt was separately rejected as
`approval_required`. Single reported model metadata is retained even on failure.

The full local suite also caught missing standalone-binary hidden imports for the
four AI modules. These are registered in the build script, and the shared import
cycle refactor from #835 has been integrated before final verification.

Additional guard falsification rejected omission of the installed Codex runtime
file, omission of OS user identity and choosing an actual model from mixed model
usage. Each failed its intended assertion; production files were restored.
