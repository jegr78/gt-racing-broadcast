# AI agent adapter requirements

Issue #817 provides the machine configuration and provider boundary for #816.
The feature defaults to disabled. The Control Center integration belongs to #821.

| Criterion | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Named machine configurations and last selection | `ai_agents.Settings` | save/reload multiple configurations | invalid fields, duplicate IDs, corrupt state | temporary machine root, profile export exclusion |
| External subscription login, no credentials stored | `Adapter.probe` | fake subscription status | API environment, API login, missing CLI, unknown status | installed CLI version/help/auth probes, no inference |
| Exact model choice | `Adapter.invocation` | requested model remains one argv element | unavailable model, no model | fake catalogue and manual model |
| Isolated execution and selectable personal extensions | `Adapter.invocation` | bounded package/output permissions | unsupported capabilities, external MCP, overlapping directories | current help/docs plus release platform matrix |
| Structured output and diagnostics | `Adapter.extract` | Codex/Claude fixtures | malformed result, permission denial, provider error | fake subprocess fixtures |

New guards must be falsified individually after the first correct commit. The
release acceptance matrix requires actual authenticated integration on Windows,
macOS and Linux in #821. Local help/auth checks are not that release evidence.

## Provider contract

`Settings(machine_runtime)` stores `ai-agents.json` at the machine runtime root.
An empty installation is disabled. Configurations have stable slug IDs, display
names, `codex` or `claude` provider, optional executable override, explicit model,
10..3600 second timeout, and isolated/personal mode. Personal instructions, skills
and MCP are separate booleans. Last agent/model/template is machine-scoped.
Profile export includes profile files and selected assets, not this machine file.
Unknown settings fields are rejected, including credential fields.

`Adapter.probe()` checks executable discovery, version, required flags and login
without inference. It returns sanitized status, version, capabilities and guidance.
Authentication environment overrides are conflicts. Unknown status is not ready.
Version baselines are Codex 0.162.1 and Claude Code 2.1.248, plus required flag checks.
`discover_models()` uses Codex's non-billable `debug models` catalogue when available;
these are suggestions, not guaranteed account entitlement or quota. Claude uses
manual names. A selected model is passed verbatim with no fallback.

`invocation(package, output, schema, probe, model)` returns argv, cwd, result path,
timeout and provider. Its caller must use a fresh dedicated output directory and
re-probe immediately before spawning. The package has no project settings or
symlinks. The runner must not inherit profile secrets or API authentication,
must handle permission requests as terminal failures with manual-run guidance,
and must cancel the process tree. Racecast writes/extracts structured output;
Claude's model gets read access only. Neither adapter uses a shell template.
`extract(raw, events)` returns an unvalidated object and actual model when reported;
#820 validates all factual values and references before creating a report.

Codex ignores user config and rules, disables hooks, plugins, apps and web search,
and applies a named permission profile denying filesystem root access with minimal
runtime reads, package reads and output writes. Shell subprocesses inherit no host
environment. Instructions and host skill discovery are disabled by default and
can be selected independently. Authentication still uses the existing CLI state.
Claude uses `--restricted`, `--safe-mode`, an explicit Read-only tool list and
empty MCP configuration; it preserves subscription login, unlike `--bare`.
Selective personal Claude configuration has not been verified in restricted mode
and is refused with manual-run guidance. Personal MCP is refused for both adapters
because external servers are outside the CLI filesystem boundary. No blanket
permission or sandbox bypass flag is used.

New adapters must define non-billable sanitized authentication/version checks,
model suggestions versus entitlement, argument-array construction, isolated
permissions and explicit limitations, structured result extraction, events and
terminal diagnostics. Unknown authentication, permission requirements and output
contracts must fail closed. Add controlled subprocess fixtures and actual platform
integration evidence before changing the provider allowlist.

## Evidence and release checklist

Local Linux checks on 2026-10-10 found Codex 0.162.1 and Claude Code 2.1.296 with
subscription authentication. A non-billable Codex `debug prompt-input` check accepted
the generated permission table and contained no host skill catalogue. This caught
and corrected a quoted dotted-key parsing error that argv-only tests missed.
Official capability references checked on the same date:

- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex filesystem permissions](https://learn.chatgpt.com/docs/permissions)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Claude CLI restricted and safe modes](https://code.claude.com/docs/en/cli-reference)
- [Claude authentication](https://code.claude.com/docs/en/authentication)

For each Windows/macOS/Linux subscription integration in #821, record versions,
requested/reported model, login reuse, absence of API fallback, isolation against
sentinel files outside the package, refusal of external writes, personal selection,
output validity, unavailable-model behavior, interactive permissions, provider
failure, timeout and process-tree cancellation. Use synthetic telemetry and no
credentials or private recordings in committed evidence. Until this matrix is
complete, the feature is not release-verified.
