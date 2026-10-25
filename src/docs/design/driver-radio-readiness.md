# Driver radio integrated acceptance

Epic #969, delivery #975. Access #970, assessments #971, shared lifecycle #972 and
providers #973 are integrated with native service #976 and browser views #974.
The native topology supersedes the earlier browser-microphone/playback proposal.
The relay, microphone and output run on the same local Solo PC.

| Acceptance | Software evidence | Result and limits |
| --- | --- | --- |
| Four presets answer current measured facts | assessment tests and signed shared-service requests | Fuel includes measured consumption/range/finish; pace includes last/best eligible comparable lap and trend; tyres include available confirmed age; short status stays brief |
| Grounded fuel warnings and reserve-aware finish | `test_driver_radio_assessment.py`, `test_driver_radio_service.py` | Crossing/rearm, gradual refill, incomplete consumption, race completion and timed final-lap assumption covered |
| Pace/tyres without AI | stable/slower/noisy/service/context replay cases | Robust comparable windows; conditional tyre advice; no fabricated wear/traffic/weather/ideal temperatures |
| Browser-independent native lifetime | native supervisor tests, controlled process IPC and browser teardown | No browser mic/playback permission; one native owner remains with all views closed |
| Explicit persistent native consent | disabled-startup, settings/revocation and producer HTTP tests | Initially off; profile enablement separate from temporary pause; unreadable settings stop capture/output |
| Prepared local wake plus continuous question | actual Vosk phrase validation and shipped private-worker synthetic smoke | Hey Jeff/Hey Engineer positives and negative sample; 2 s pre-roll, 16 s bounded utterance; no model training |
| English/German input, English output | selected-language adapter tests and fake provider payload inspection | Both configurations supported; first synthetic German dictation was inaccurate; hardware/voice-quality check remains deliberate |
| Replaceable STT, no implicit cloud | Vosk/whisper/Google adapter tests | Explicit selected provider only; Google receives wake-gated PCM, never continuous ambient streaming |
| Free questions and short follow-ups | refreshed fuel/pace/tyre question tests | Four-turn volatile topic context; old session/context/setup epoch rejected; vehicle/broadcast commands refused |
| No raw-audio persistence | Whisper stdin test, private capture worker and bounded PCM tests | Raw microphone PCM stays in memory; model downloads and bundled fixtures contain no personal recording |
| One native audio owner and cancellation | real nested-process cancellation, fake-player lifecycle and urgency tests | Stops before releasing slot; expiry, mute, pause, settings loss and shutdown cancel owned work; no barge-in |
| Optional bounded Studio-Q/AI | provider and private-transport tests from #973 | Text first, no silent voice/provider fallback; unchanged deterministic facts on failure; fixed safe diagnostics |
| Shared driver UI and narrow access | real signed HTTP and `tools/radio_browser_check.py` | Driver-only desk, Solo card, shared IDs, setup conflicts, XSS, revocation and closing views covered |
| Producer controls stay local | owned-relay/profile preflight and Origin/Host tests | Driver assignments and provider consent cannot be changed through the public Console |
| Source/package/frozen discovery | explicit binary imports, private-entry tests and distributable build | No mandatory Python package dependency; optional native libraries/models install explicitly with pinned digests |
| Documentation and screenshots | Driver-Radio wiki, refreshed Console/Director/Control Center and desktop/tablet/phone screenshots | Synthetic data and development badge; original public role boundaries retained |

## Defaults and practical bounds

The service keeps 32 messages, four queued speech items and four conversation
turns. Routine messages expire after 30 seconds; urgent fuel after 15 seconds.
Automatic urgent speech is brief; requested presets retain their required facts.
Speech and local/cloud transcription have separate eight-second work budgets.
Capture/model startup and idle heartbeat have a 20-second watchdog. Native
worker/device failures use a five-second retry interval. Speech buffers/cache and
provider pipes keep the bounds documented in the provider/service contracts.

The browser component has no microphone acquisition or speech playback. Static
JavaScript contains no profile/session data. Every radio data read and mutation
uses current Console identity and explicit profile assignment. Local producer
configuration uses the existing exact loopback/Host/Origin boundary plus owned
relay/profile/protocol checks. Revocation clears private text and forms in open
views. Dirty forms retain their original revision, so a concurrent update cannot
silently overwrite another view's setup.

## Validation procedure

Run focused radio test files, then repository lint, the complete stdlib suite,
relay regression and distributable self-verification on the committed head.
`tools/e2e.py --visual` also runs the real radio browser acceptance helper. Its
standalone command is `runtime/pw-venv/bin/python tools/radio_browser_check.py`.
`--shots src/docs/wiki/images` refreshes the synthetic wiki images.

Guard fault injection runs only in memory. Device/process primitives and invalid
archive filesystem writes are intercepted in the affected mock tests. Do not run
the disabled shared-checkout mutation harness. The native pass independently
falsified consent, pause, output freshness, revisions, wake gating, download hash,
archive parent traversal, prepared phrase and private-input limits.

## Explicit operator checks outside ordinary CI

No live microphone capture, paid cloud request or model training was authorized
or performed for this delivery. These checks remain deliberately separate from
software acceptance:

- Measure configured Studio-Q cold/warm latency with the explicit smoke command.
- Verify real microphone permissions, sharing with OBS/Discord, unplug/replug and
  output routing on each producer OS/device. Synthetic failure fixtures cover the
  lifecycle mechanics, not physical device coexistence.
- Evaluate missed/false wakes, English/German recognition and own-output feedback
  with the driver's voice and ambient noise. Synthetic English wake-plus-question
  passed through the actual worker in 3.24 seconds; the first German synthetic
  phrase was poor. No engine-accuracy or hardware-latency guarantee is implied.

Remote/cloud relay plus a driver-side audio helper, arbitrary wake-model
creation, automatic language switching and spoken barge-in remain outside the
confirmed first-version topology. Optional services can remain unconfigured;
all deterministic presets and assessments continue as text.
