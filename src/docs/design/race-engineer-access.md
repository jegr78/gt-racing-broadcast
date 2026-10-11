# Race Engineer access model

Parent #822, delivery #823. #809 and every child #810-#814 are completed.

| Requirement | Production path | Positive test | Negative test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Additive Crew flag and legacy rows | CrewSource, role resolver, webhook, Crew editor | moved header, seven-field round-trip, Discord preserved | absent flag, old cached rows, Engineer has no control rights | synthetic Crew editor and real relay HTTP |
| Producer inheritance only | resolve_roles, Console policy | Producer and explicit Engineer | Director/Commentator/Race Control without assignment | signed identities on real Console routes |
| Profile opt-in and solo POV availability | profile-owned access store, producer Telemetry card | enable/save/reload | default off, unavailable profile, malformed state | producer UI and relay read after change |
| Every-request authorization and revocation | Console gate and access contract | current token/role/enabled | missing token, removed role, disabled access, mutation paths | existing open page clears on next poll |
| Explicit historical grants and context | access store keyed by stable source identity | grant/list/revoke own finalized recording | guessed ID/path, foreign profile, deletion/replacement/duplicate identity | producer sharing UI and scoped reference status |
| No private producer data | allowlisted access response | opaque reference ID/label | filename/path/IP/stream/credential fields absent | inspect actual HTTP JSON |
| Existing independent rights/chat | Console policy/resolver | combined roles and Crew chat | Engineer-only operational mutations refused | signed role matrix |
| Producer sharing disclosure | Telemetry controls, wiki | context-sharing and already-transmitted notice | no silent archive grant | browser and published wiki |
| Launcher and later route contract | Console launcher and protected access page | capability-aware card, feature state | denied/unavailable page, no full desk yet | desktop/mobile synthetic screenshots |
| Cross-platform and delivery | tests, lint, build, security review | canonical gates and browser | individually falsified guards | green CI, merge and tracker reread |

## Boundaries

Access settings live in the current profile runtime, separate from recording data
and authentication. The switch defaults off. Grants bind finalized recording
identities and source signatures inside that profile; names or arbitrary paths
never authorize a Console read. Shared references always include their analysis
context. Revocation cannot recall data already transmitted to a browser.

This delivery adds access and a protected availability page. Live ingestion,
analysis data and the final Engineer desk follow in #824-#826. The localhost
Control Center remains private, and Engineer endpoints have no mutation routes.


## Verification and review

The signed HTTP role matrix covers missing identity, Engineer-only operational
refusals, Producer inheritance, role removal, profile feature disablement and
source grants. Producer HTTP tests assert status and semantic errors for foreign
origins, profile changes, stale revisions and invalid reference IDs. The real
browser exercises the production Relay Crew read/write path, role checkbox
round-trips, opt-in, XSS-safe labels, sharing/revocation, open-page clearing and
delayed responses during profile switching.

Independent review reproduced two findings. A successful empty Sheet roster
retained the previous roles; refresh now atomically accepts empty results while
fetch failures remain distinct. The six-field compatibility projection dropped
Engineer from `/crew/data`; the production route now uses canonical seven-field
members. Both failures have real HTTP red/green regressions. Role resolution reads
one roster snapshot to avoid mixing capabilities from different revisions.

Limits: 128 explicit grants, a 128 KiB access document and a producer inventory
bounded to 4096 recording entries. Console readers inspect only granted sources;
they do not run the producer archive inventory or build recording indexes.

Twenty-one assurances were falsified individually from the committed implementation:
role requirement, opt-in, source availability for reads and writes, enablement
type, optimistic revision, reference ID format, recording-root boundary, source
identity, source signatures before parsing and after metadata checks, grant/archive/
document bounds, producer profile binding, successful empty roster, canonical Crew
API, one role snapshot, newest settings responses, response-profile binding and missing packaged pages.
Every mutation failed its intended assertion and was restored before final gates.
All 20 ordinary visual surfaces were clean; the Engineer browser workflow runs in
visual CI. Changed screenshots use synthetic data and the dev-build UI.

The canonical build guard caught the new page missing from the flat distributable.
The build now copies and verifies it. Runtime discovery resolves the Engineer
page alongside the already-discovered Console page, covering source, flat-package
and frozen layouts without a machine-specific path. Missing pages fail closed.
