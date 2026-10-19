# YouTube audience metrics

Issue #933 builds on the delivered manual widget foundation #932. Target selection
is explicit per profile and never follows incoming commentator feeds. The independent
asset-alias PR may finish CI during development; integration and final verification
will include its merged main state before this issue is landed.

| Requirement | Production path | Positive behavior test | Error behavior test | Runtime proof |
| --- | --- | --- | --- | --- |
| Minimum optional connection | YouTube adapter, relay-owned runtime credentials | Public API key enables requested metrics via header | Invalid key/quota/setup errors are bounded safe messages; manual stays usable | Actual local connection dialog with fixture transport |
| Explicit output selection | Pure target parser, verified candidate, public profile config | Video ID/URL and channel ID/handle resolve canonical IDs and titles | Dynamic channel/live shortcut, foreign URL, stale candidate rejected | Verify, inspect resolved output, activate replacement without touching input feeds |
| Likes/live viewers/subscribers | Adapter to shared Sample/cache | Real zero and raw exceeded goal preserved; rounded subscribers approximate | Missing/hidden/invalid numbers never become zero; subscribers cannot exact-net | All shared scenes consume cached values; subscriber preview/live show approximation |
| Broadcast lifecycle | Video resource parser and operator status | Upcoming, live, ended, inaccessible remain distinct | Ended stream hides viewers even with lingering count; no next-video discovery | Fixture lifecycle transitions through shipped endpoint and UI |
| Central bounded polling | One relay-owned scheduler, shared MetricCache | 30s video / 60s channel schedule only for needed definitions | Disabled/unused metrics stop; quota/rate/network backoff; readers never fetch | Several browser sources share one recorded provider request sequence |
| Honest stale interval | Monotonic cache observation, relative renderer deadline | Transient last-good held until 120 seconds from success | Repeated failures or producer wall-clock rollback cannot extend hold | Fake clock advances on real data/renderer contracts |
| Persistent net baseline | UI writer captures cached source Sample under lock | Fresh exact likes can deliberately capture net baseline, restart stable | Stale/approximate/wrong source or concurrent definition change rejected | Automatic reset/capture click with unavailable-goal but available-source sample |
| Generation-safe replacement | Separate setup attempt and active connection generations | Pending verification leaves old working target/key active until activation | Disconnect/cancel/replace defeats late API responses and credential writes | Blocked fixture request + disconnect while handler stays responsive |
| Credentials stay local | Atomic private runtime files, public target profile file | Validated key persists only after explicit activation | Failed verification/persistence preserves old usable setup; no secret echo | Bundle/Look/overlay/log/screenshot assertions with secret sentinel |
| Local setup boundary | Loopback provider admin, exact origin/Host, UI proxy | Active profile can verify/activate/disconnect/read cached source | Tailnet, foreign origin, malformed body, wrong profile, console/Funnel denied | Actual HTTP role/origin tests and no credential POST to a foreign port holder |
| Profile identity and portability | Immutable owned slug, public target config outside Look | Export/import carries non-secret targets and prompts for local key | Display name cannot masquerade as slug; profile A cannot accept B's setup | Two profile fixtures with different display names, export and reconnect |
| Operator workflow | Source picker and separate provider connection dialog | Select metric, inspect permission/precision/status, reconnect | Delayed old profile responses do not overwrite current form; source change not silently reset | Actual browser input, pending/verified/activate/disconnect and live scene update |
| Existing capture/chat/feeds | Provider worker separate from control/feed loops | Provider and manual share runtime without changing relay input source | Network failures/blocking provider request do not call OBS/chat/ingest controls | Recording fakes and existing full scene/relay regression suite |
| Shipping and docs | Build resources, wiki and development screenshots | Frozen/module paths resolve and cadence/setup/precision documented | Empty connection leaves existing manual/no-widget overlay unchanged | Build verify and real connection/editor screenshots in the same PR |

## Decisions

Use the official YouTube Data API with the operator's API key in the
`x-goog-api-key` header. Call only fixed official endpoints. Parse pasted URLs
locally; never fetch the pasted URL, scrape the page or infer output selection
from rotating input feeds. Video and subscriber channel targets remain explicit.

A verified candidate contains canonical IDs/titles in its public status and a
key in relay memory only. Explicit activation stores the connection; verification
alone retains the old working setup. Candidate expiry, cancellation and active
connection generations prevent late responses from reviving disconnected state.

Shared definitions stay in `audience.json` under the UI writer. The relay owns
provider config, credentials, polling and cache. Public targets travel with the
profile; credentials live only under the owned runtime profile. Setup proxies
verify the relay's protocol marker and owned profile slug before sending keys.
`RACECAST_PROFILE_NAME` is a display name, so it is not the profile identity.

Public subscribers are rounded down to three significant figures. They always
carry approximate source precision and do not support exact net subscriber goals.
Concurrent viewers are not unique/new viewers. Likes may support an exact net
change after deliberately capturing a fresh source baseline.

Monotonic observation time bounds freshness independently of wall-clock changes.
Transient failures preserve the original deadline; successful requests refresh it.
Explicit disconnect, invalid authorization, changed target, hidden metrics or
ended/offline viewers are unavailable immediately. The live renderer already
expires automatic values using relative client deadlines.

## Credential and transport boundaries

Public targets and titles live in the profile root, outside Look snapshots.
Credentials live only at the owned runtime profile root. A shared non-secret
connection identity binds the two atomic files. A failed public-config commit
restores the old private state when possible; mismatched files on restart require
reconnection. Verification is RAM-only until explicit activation. Disconnect
stops RAM publishing immediately and reports storage/cleanup failures honestly.

Provider HTTP uses the shared User-Agent helper, fixed HTTPS endpoints, no
redirects, finite timeouts and bounded success/error bodies. API keys are headers,
never query strings. A local redirect fixture reproduced the standard client's
cross-host key forwarding; the no-redirect policy must prevent the second request.

One relay-owned worker handles bounded setup work and demanded polling. Start it
only after successful control-server binding. Network work runs outside state
locks; active and setup generations reject late results. Readers never fetch.

## Primary references

- [Videos list](https://developers.google.com/youtube/data/reference/rest/v3/videos/list)
- [Channel statistics](https://developers.google.com/youtube/data/reference/rest/v3/channels)
- [Channels list](https://developers.google.com/youtube/data/reference/rest/v3/channels/list)
- [API key headers](https://docs.cloud.google.com/docs/authentication/api-keys-use)
