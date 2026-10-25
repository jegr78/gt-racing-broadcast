# Driver radio speech and optional explanations

Epic #969, delivery #973. Provider work attaches to the shared message service;
it never delays deterministic text, relay HTTP, telemetry or broadcast workers.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| English Studio-Q only | shipped GoogleSpeech through http_util | fixed voice, LINEAR16 WAV, explicit API-key header | missing key, HTTP/redirect, invalid/oversized audio | fake HTTP request and response inspection |
| Explicit profile service consent | profile provider flags and worker factory | speech and selected existing agent | default off, revoked selection, disabled voice | actual service settings and zero-call counters |
| Bounded synthesis/cache | deadline, WAV validator and LRU | reusable exact utterance | excessive response/duration, cancellation, cache eviction | bounded byte and invocation counts |
| Grounded optional AI | compact fact package, existing isolated Adapter | supplied fact IDs/values | unknown fact/number, personal extensions, provider failure | fake CLI and result-schema validation |
| Isolated short-lived execution | existing process-tree helpers and live budgets | bounded configured CLI result | timeout, output flood, descendants, cancellation | owned Python fixture processes on supported OS |
| Text-first and current attachments | independent enrichment workers, Service.attach | text visible while worker blocked | expiry/profile/session/context/access change during work | events and real shared message state |
| No credential/recording disclosure | private environment and allowlisted facts | only selected current facts leave process | raw provider errors, private paths, unrelated history | outgoing request/package and public JSON inspection |

## Defaults and consent

Profile settings add `providers` with `speech=false` and `agent=null`. Speech
requires both profile voice enablement and explicit Google speech selection,
plus the established machine environment key `RACECAST_GCLOUD_TTS_KEY`. The key
is sent as a header to the fixed HTTPS Google endpoint; redirects are refused.
It is never included in URLs, public status, reports or exception messages.
No fallback voice or provider exists. Missing services leave deterministic text.

A selected agent must already exist in machine AI settings, be enabled there,
and use the existing isolated subscription CLI adapter. Personal extensions are
not silently enabled for background live work. A compact temporary package
contains only current allowed facts, the radio request and output schema. The
existing post-session package/report workflow remains independent. The worker
uses the existing machine lease and process-tree cleanup helpers; busy analysis
means a skipped explanation, never a delayed deterministic answer.

Speech has an eight-second network budget, one worker, a two-MiB WAV bound and a
25-second utterance limit. Identical utterances use a private in-memory LRU with
16 entries and eight MiB total. AI has its own eight-second live deadline,
64-KiB stream/result bounds and at most one attempt per 30 seconds. Urgent fuel
never waits for AI. These are bounded engineering defaults; configured Studio-Q
cold/warm latency is a separate explicit smoke check, not an ordinary CI call.
No paid service call or live microphone capture is part of synthetic validation.

## Result boundaries

AI explanations are labelled interpretation. They must cite supplied fact IDs;
structured numerical claims must exactly match the supplied value and unit.
Numerical text must be covered by those claims. Missing traffic, wear, weather
and ideal-temperature channels cannot become observed facts. Reject unsupported
output and keep deterministic text. This validation checks grounding, not the
truth of an interpretation. No provider output can execute a control operation.

Provider failures publish fixed status codes, retain text and discard raw remote
diagnostics. Every completion rechecks message epoch, freshness, current requester
permission and service selection. Cached bytes do not authorize playback.

## Primary references

- [Google synthesis request/response](https://docs.cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize)
- [Google audio format and controls](https://docs.cloud.google.com/text-to-speech/docs/reference/rest/v1/AudioConfig)
- [Google voice catalog](https://cloud.google.com/text-to-speech/docs/voices)
- Existing adapter isolation and process ownership in `ai_agents.py` and `ai_jobs.py`
