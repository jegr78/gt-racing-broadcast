# Local Telemetry workspace

Issue #887 replaces the unreleased Control Center analysis page with a dedicated local workspace. The agreed workflow is inspired by the documented [Coach Dave Delta lap comparison](https://coachdaveacademy.com/documentation/how-to-compare-laps-in-delta/): explicit session/reference identity, broad stacked traces and supporting detail panels. Racecast retains its own styling and existing measurement/provenance rules.

## Responsibilities

`racecast telemetry ui [--no-browser]`, optionally with `--profile NAME`, opens `/telemetry` through the existing localhost UI service. A second launcher reuses that service. Closing a tab leaves the service and AI work running. Explicit **Stop UI service** disconnects both interfaces; an active AI job requires an additional acknowledgement. Relay, Companion and stream services retain their existing lifecycle.

The workspace has four tasks:

- **Archive** is the landing page, with import, recording metadata and an entry to the last comparison.
- **Analysis** gives wide aligned traces priority. Reference A and analysed lap B remain explicit. A common distance window/cursor links traces to optional map, sector and context detail. Expanded channels retain their bounded on-demand raw windows.
- **Reports** contains profile-owned AI histories and text-only validated results. Explicit preview/start/cancel and package export remain available from the selected analysis.
- **Data management** contains recording context/preparation/templates, CSV export, track/sector editing, vehicle reference maintenance and machine-wide AI configuration.

The Control Center retains producer Engineer opt-in/grants, broadcast replay including the on-air confirmation, and the workspace entry. The remote Race Engineer Desk remains read-only and keeps its existing roles, opt-in, opaque references and historical grants. The new local workspace does not expose an additional remote archive or editor.

Live UDP reception, recording, replay and live Engineer computation remain relay-owned. Archive work and AI operate on existing profile files with no running relay.

## Profile boundary

An **analysis profile** is independent of the **broadcast profile**, as defined in [the glossary](../CONTEXT.md). Each local tab selects its own analysis profile. Analysis URLs and requests explicitly carry it; the server validates an existing profile name and binds callbacks to a request-local context. It never rewrites the broadcast pointer or process environment.

Analysis subprocess operations have an explicit `--profile` in their child argv. Job admission distinguishes the same operation in different profiles; joining and polling require the matching profile. A scoped analysis request cannot dispatch a broadcast operation. The existing machine-wide AI admission lock still allows one AI run across all profiles and CLI/web clients.

Recording identities, context/source revisions and stale-response generations remain enforced. Profile changes reset selections and invalidate pending UI responses. Report links select the explicit analysis profile and then verify recording identities. Existing local report URLs forward into the dedicated destination.

## Data ownership

Storage formats and directories remain unchanged:

- Profile archives, CSV/index artifacts, preparations/templates and AI histories stay under the profile runtime.
- Recording context/history and local draft backup remain bound to the recording/profile identity.
- Track/sector overrides, external shift-reference cache and AI provider configuration remain machine shared. Their editors label this scope explicitly.
- Recording/setup-specific shift targets remain contextual snapshots, separate from external reference data.

The same local origin retains existing browser drafts. UI preferences and the last comparison use profile-specific browser storage. Destructive operations retain active-recording and AI-admission safeguards; a profile switch during a confirmation invalidates that operation.

## Verification

`tests/test_telemetry_workspace.py` covers request-local profile resolution, concurrent requests, child pinning, invalid profiles and local-origin refusal. Existing telemetry/context/AI tests retain their domain assertions against the extracted production functions.

`tools/e2e-telemetry-workspace.py` uses temporary copies of the source app, two real profile archives, real callbacks and CLI subprocesses, without a live relay. It checks independent profile/context/export ownership, delayed deletion confirmation, launcher reuse, distance cursor/zoom, reload and responsive tasks. `tools/e2e-ai.py`, `tools/e2e-engineer.py` and `tools/e2e-telemetry-replay.py` cover the preserved AI, sharing and broadcast replay workflows. Full visual acceptance includes the dedicated tasks and the retained Control Center controls.
