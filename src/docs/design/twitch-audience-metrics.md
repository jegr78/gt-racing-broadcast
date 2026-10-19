# Twitch desktop authorization and audience metrics

Issue #934 extends shared counters/goals from #932 and uses the local provider
administration, credential pairing and monotonic cache contracts delivered with
YouTube #933. Twitch owns its device approval and one-use refresh state. No
provider credential passes through the browser or public overlay data.

## Authorization decision

Use Twitch's public Device Code Grant for this desktop application. The operator
registers a public Twitch app and supplies its client ID. There is no embedded
client secret and no local OAuth callback listener. Approval takes place on
Twitch's hosted activation page, using a short-lived user code.

Validate the returned activation URL against HTTPS, `www.twitch.tv`, `/activate`
and the documented optional `public=true&device-code=<user_code>` query. The
query's code is the public user code, not the private `device_code` used by the
server. Reject extra parameters, userinfo, ports, fragments and foreign hosts.
The browser receives the user code and validated approval URL only.

The relay owns OAuth requests, token validation, refresh, credential persistence
and polling. The Control Center uses the profile-bound loopback admin proxy.
Never send access tokens, refresh tokens or the private device code to a browser,
overlay, profile bundle, Look snapshot or log. Public target/client ID selection
can travel with a profile; an imported profile requires local reconnection.

For the full metric set, request only `channel:read:subscriptions`. Viewers do not
need that permission, so missing subscription scope or authorization for another
broadcaster must leave permitted viewer counts usable. Anonymous broadcast chat
continues independently, without a new OAuth requirement.

Validate tokens before use and on maintained sessions. Match the returned client
ID and account identity; use granted scopes, not requested scopes, for capability
checks. A protected subscription request requires the authorized user to own the
selected broadcaster channel. The output target is explicit and never follows
Endurance input-feed handovers.

Device and public refresh codes are single-use. Once an exchange succeeds,
retain the new token pair in relay memory if local persistence fails, then retry
storage without repeating the exchange with the consumed code. Publish the new
connection only after persistence. A process restart before successful storage
requires reconnection. Separate pending approval attempts from the active
connection, so cancelled or late responses cannot replace a working setup or
revive a disconnected connection.

Polling intervals, approval expiry, refresh scheduling and backoff use monotonic
time. Honor pending/slow-down responses; Twitch's documented pending payload is
HTTP 400 with `message: authorization_pending`. Accept a token success only after
validating its complete shape and identity. Expose fixed safe diagnostics rather
than raw OAuth error bodies.

## Metric semantics

Current viewers come from Helix Get Streams. An empty result is offline and
unavailable; a live result with `viewer_count: 0` is a valid zero. Viewers are not
unique viewers or a session-new-viewer count.

Active channel subscriptions come from Get Broadcaster Subscriptions `total`,
including funded subscription types as reported by that endpoint. Never use the
length of one paginated `data` response or subscription points as the count. A
captured baseline measures net active-subscription change, which can be negative.
It is not a count of all subscription events during the session. Event-based new
subscriptions and followers remain outside this delivery.

Transient request failures use the shared bounded stale policy. Disconnect,
revocation, missing permission, wrong owner and offline metrics are unavailable
immediately for the affected source. Credential or provider failure must not
reset manual goals or control capture, chat, scenes, audio or feed ingest.

## Evidence plan

| Requirement | Production path | Positive test | Error test | Runtime proof |
| --- | --- | --- | --- | --- |
| Desktop authorization choice before code | This design and provider connection dialog | Public client/device approval | No callback/client secret required | Actual fixture dialog with hosted approval link |
| Device timing and secrecy | Relay-owned device attempt | Pending to approved, granted scopes/account | Slow-down, denial, expiry, malformed token, unsafe URI | Fake-clock requests through actual admin routes |
| Single-use refresh | Serialized token manager and atomic runtime store | Refresh rotates once and persists | Write failure retries storage only; cancellation drops late result | Blocked request and failed disk fixture with intermediate state assertions |
| Explicit output channel | Canonical login/ID resolver, public profile config | Verified broadcaster name/ID | Foreign URL/nonexistent target/wrong owner | Confirm selected output without changing inputs |
| Viewer count | Helix adapter/shared cache | Real live zero and live count | Empty/offline and bad values unavailable | HUD/Intermission live fixture transitions |
| Paid subscription total | Helix subscriptions adapter | `total` differs from page length/points | Missing scope, wrong broadcaster, revoked token | Shared goal widget displays authorized total only |
| Net active change | Shared fresh-source baseline writer | Explicit exact baseline persists | Stale source and changed channel rejected | Reset/capture UI with cached source |
| Central polling and rate limits | Provider service scheduler | Multiple readers share requests | Disabled/unused stop; backoff and original stale deadline | Recorded transport calls plus fake monotonic time |
| Profile and credential boundaries | Loopback admin, local runtime token file | Active profile reconnects | Foreign origin/socket/profile denied; exports exclude secrets | Real HTTP and bundle fixtures |
| Existing broadcast paths | Provider worker isolation | Manual/capture/chat continue | API failure cannot call OBS/ingest actions | Recording fakes and full regression suite |
| Operator docs and screenshots | Wiki/setup/editor UI | Connected identity, target and permissions clear | Imported profile prompts for local auth | Development-build actual screenshots in same PR |

## Primary references checked on 2026-10-19

- [Twitch OAuth and Device Code Grant](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/)
- [Refresh tokens](https://dev.twitch.tv/docs/authentication/refresh-tokens/)
- [Validate tokens](https://dev.twitch.tv/docs/authentication/validate-tokens/)
- [Helix API reference](https://dev.twitch.tv/docs/api/reference/)


## Credential rotation and restart contract

Before exchanging an active one-use refresh token, atomically persist a private
`refreshing` phase bound to the public connection identity. If this write fails,
do not exchange. If exchange succeeds but saving the new pair fails, keep the new
pair in RAM and retry local persistence only. Rolling back a failed public commit
must restore the `refreshing` phase, never an old ready consumed token. A restarted
process encountering this phase requires authorization again. A lost exchange
response is ambiguous and likewise requires reconnecting. Generation checks drop
late responses after disconnect, cancellation or replacement.

A separate temporary-file experiment exercised phase-before-exchange, failed
public commit/rollback, no exchange after a failed phase write, RAM-only persistence
retry and restart without reusing a consumed refresh token. These experiments
inform repository TDD and are not final delivery evidence.

## Timing and permission policy

Validate saved authorization before use and at least hourly while maintaining a
session, including when no widget needs provider metrics. Follow Twitch's reactive
401 guidance for refresh; distinguish expired authorization from revoked sessions
and metric-specific permission failures. Use monotonic deadlines for polling,
approval expiry, validation and backoff, and include response/RPC/lock time in
sample age. No failed read or refresh can extend the original cache deadline.

The requested scope is only `channel:read:subscriptions`. Granted validation
scopes and the authenticated user ID determine actual subscription capability.
Do not turn a subscription scope/owner failure into zero or make permitted viewer
counts unavailable. The ordinary anonymous chat connection needs no changes.

## Input and publication boundaries

Parse only explicit Twitch channel URLs/logins or numeric channel IDs. Resolve a
canonical ID/login/display name before activation. Provider HTTP uses fixed
HTTPS OAuth/Helix endpoints, headers for access tokens, form bodies for private
codes/refresh tokens, no redirects, finite timeouts and bounded JSON/error reads.
Public approval URL validation permits only the official activation host/path
and the matching public user-code query. Never put the private device code in a
browser. Invalid metadata must fail before it can be activated or persisted.

Source identities are canonical channel/metric identities, independent of
client ID, access token, refresh token and display names. Export only public
target/client-ID selection; preserve baselines and connection targets outside
Look snapshots. An imported profile needs local authorization. Connection status
shows cached metric availability, granted capability and durable storage errors
without overwriting edited fields or a new profile's form.


## Verification entry points

`tests/test_audience_twitch.py` and `test_audience_twitch_transport.py` cover local
selection, documented request forms, scope/count semantics, bounded transport,
redirect refusal and safe rate-limit diagnostics. Device/rotation/storage tests
exercise one-use exchanges, cancellation, expiry, permission and phase contracts.
`test_audience_twitch_connection.py` adds real-file restart, rollback, refresh
backoff, cache age, public bundle import and concurrent generation fixtures.

`test_audience_twitch_http.py` uses actual loopback HTTP and valid Console roles;
`test_audience_twitch_ui.py` checks producer callbacks, origin refusal and cached
baseline capture. Shared proxy fixtures check protocol/profile/provider before
credential forwarding and on command replies. No provider credential or real OBS
instance is needed.

`runtime/pw-venv/bin/python tools/e2e-twitch-audience.py` drives the shipped manager,
public approval link, edited-selection guards, explicit activation, failed-save
retry without OAuth repetition, cached baseline and actual HUD/Intermission
pages. It checks live zero, offline transitions, negative active change,
disconnect and late profile replies. Add `--shots` only to regenerate the inspected
development-build wiki and identical slide images. The existing YouTube browser
and shared manual-widget flows remain part of final verification.
