# Driver radio access and setup

Epic #969, first implementation delivery #970. This is the access/setup foundation;
message generation, assessment, native capture/playback and browser controls follow
in #971-#976. The user authorized implementation on 2026-10-25.

## Requirements before implementation

| Criterion | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Explicit profile opt-in and assigned identity | profile radio store, Console gate | signed assigned driver reads access | Director/Engineer/unassigned Producer denied, disabled/foreign profile denied | ephemeral relay HTTP |
| Independent Engineer and producer rights | Console policy and existing Engineer gate | combined assignment works | driver cannot read references or control OBS | signed HTTP role matrix |
| Narrow setup and service writes | radio session state | lap/time setup, compound, reserve, explicit service | unsupported fields, nonfinite values, stale revision/session/context | HTTP response and subsequent read |
| Current authorization and request origin | radio gate and origin check | current token, same origin | revoked token/assignment, foreign origin, malformed framing | HTTP status plus semantic error |
| Local native configuration distinct from remote identity | loopback administration and profile settings | explicit enablement, mic override, EN/DE selection | public/Console administration, voice setup writes | real HTTP and persisted settings |
| Conservative session lifecycle | generation/session/context binding | matching revision writes | reset/correction rejects obsolete writes, clears declarations | live binding changes in HTTP fixture |
| Bounded fail-closed storage | validated atomic profile document | round-trip and optimistic save | corrupt/oversized documents, excess identities, foreign paths | temporary profile stores |

## Access matrix

Producer administration is `GET/POST /driver-radio/admin`, restricted to the
relay's loopback peer, loopback Host and matching browser origin. It edits only
radio opt-in, explicit driver subject keys and native configuration. Every
admin response identifies its profile; writes must echo that profile plus the
expected settings revision so a stale producer client cannot edit a new profile. No Console
role, including Producer, implicitly acquires driver access. Assign a signed
Console subject explicitly in this profile. This uses existing normalized identity
keys, without adding an unrelated Crew column or changing legacy Crew rows.

`GET /console/driver-radio/access` and `/setup`, and `POST /console/driver-radio/setup`
and `/service`, require current Console identity plus assignment and profile opt-in.
Each request rereads authorization; the response never exposes the assignment list,
local microphone name, paths, archives or credentials. A driver assignment does not
imply Engineer, Director or Producer. Existing Engineer opt-in/grants remain separate.
Remote clients cannot change native configuration or write general telemetry context.
Unprefixed radio endpoints are absent. The direct panel must use the existing
Console login/signed-link entry on the same Console origin before embedding radio;
no tokenless local-panel bypass. Browser integration belongs to #974.

## Data and lifecycle

Profile-owned `driver-radio-access.json` stores version, revision, enabled, driver
subject keys and voice configuration. Defaults are disabled, no assignments,
voice disabled, configured default microphone, English input. Microphone override
is an opaque bounded device identifier, never a command. Supported input languages
are `en` and `de`; output is always English Studio-Q. Persistent voice enablement
permits the future #976 native service to start with the relay. Listening pause is
volatile worker state, distinct from disablement and remote Console login. No
voice worker, capture, playback, arbitrary prompt or spoken write is added here.

Setup is volatile, relay-owned and shared by assigned drivers of the single Solo
source. Every write carries `binding` containing generation, session_id and
context_revision, plus `expected` setup revision. Setup fields are nullable
`laps`, `duration_min`, `starting_compound`, `fuel_reserve_l`; one race length mode
at a time. A setup write replaces the complete declaration. Missing fields stay
unknown. Inputs have finite bounds and cannot modify shared telemetry context.
A service confirmation explicitly declares `compound` and binds the current lap
ID. Only the latest confirmation is retained, with server timestamp and revision.
Replaying an old revision cannot confirm another service. Setup corrections retain
the latest explicit service; starting compound never overwrites a service.

A generation, session or shared context revision change clears setup and service,
advances the revision and requires renewed declarations. This conservative reset
avoids carrying uncertain race/service assumptions across corrected evidence.
No active session means setup writes return `session_unavailable`; access remains
readable without telemetry. Setup read is available with an empty binding while
waiting. Declarations do not claim measured wear or alter archived recording facts.
Missing setup does not block future deterministic measured answers. Shared
telemetry ingestion runs when either Engineer or radio is opted in. The two
HTTP gates stay independent; radio never enables Engineer archive access.

All successful reads and errors use `Cache-Control: no-store`. Errors have
`error.code` and `error.message`; conflicts return 409, authorization 401/403,
invalid input 400, missing route 404 and unavailable/corrupt storage 503. Remote
writes require JSON with bounded unambiguous Content-Length and reject foreign
Origin/Referer and cross-site browser requests. Native configuration is never
returned on remote routes. Revocation stops the next request; future message/audio
workers must also bind their work to current access revision before publishing.

## Review and measured evidence

The signed HTTP fixture exercises the production relay handler with current
Console tokens, assignment changes, same-origin rejection, producer profile
binding, semantic errors, session reset and setup/service round trips. A delayed
body test pauses the actual request while token or assignment revocation lands;
the write then returns 401 or 403 before changing session state.

The separate security review found a shared-ingestion dependency on Engineer
opt-in. A real TelemetryStore/worker regression first failed with radio alone,
then passed after either feature could enable collection. Engineer access remains
disabled in that case and no recording starts. Code review also reproduced an
oversized-integer conversion error; numeric bounds now reject it without float
conversion. Corrupt storage fails closed with 503 and a stable semantic error.

Twenty-four assurances were falsified individually from committed source and
restored after the intended assertion failed: availability, opt-in, assignment,
settings revision/schema, driver count, native configuration schema, document
bound, foreign symlink, setup fields, numeric bounds/types, exclusive race length,
service compound, session reset, active session, setup revision, evidence binding,
remote origin, admin origin/profile, JSON content type, delayed token revocation
and radio-only ingestion. No external speech or AI service was called.

No browser surface changes in this delivery. Native configuration is a stored
contract for #976, not an active microphone or playback service. Remaining remote
presets, messages and audio routes are intentionally absent until their owning
implementation deliveries.
