# Grounded live voice questions (#1065)

Preparation uses the existing feature agent, isolated LiveAI adapter, compact
fact package, output validator, provider worker and acknowledged playback slot.
It retains the current eight-second AI budget and existing attempt limit.
Producer-PC latency is an acceptance measurement, not a prerequisite for preparing
and synthetically verifying the implementation. No producer-PC result is claimed.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Keep known questions fast | Existing question router and baseline speech | Known keywords retain deterministic text without waiting for AI | Commands remain refused, disabled agent keeps explicit unsupported answer | Actual service and native-worker fixtures |
| Answer unknown questions using the selected existing agent | Native question service, LiveAI prompt and existing Enricher | Frozen question, allowed telemetry facts and bounded conversation reach the configured engineer agent | No agent, disabled feature, stale/replay data, changed profile/session or cancelled ticket produces no live answer | Captured fixture requests and shared message state |
| Preserve grounded output | Existing explanation schema and fact validator | Exact supplied fact IDs, values and units rendered by the server | Invented numbers, facts, units, unconditional controls or unsupported provider output retain fallback | Actual LiveAI package/result validation |
| Speak the answer once | Existing audio worker waits for the pending live answer | Validated answer becomes speech input and one native playback | Timeout, rate limit or provider failure yields the existing fixed fallback; no stale or duplicate speech | Controlled AI/audio barriers and fake native player |
| Preserve independent text access | Existing history, conversation and HUD policies | Labelled AI interpretation is available as answer text | Muted/no-speech output cannot replay it later; AI prose never becomes deterministic HUD text | Existing message readers and playback lifecycle |
| Bound the wait | Existing eight-second budget plus message expiry | Timely completion unblocks normal synthesis | A late result cannot overwrite a timed-out answer or audio already selected | Fake monotonic clocks and blocked provider fixtures |
| Prepare producer measurement | Maintainer benchmark reusing LiveAI and configured speech | Reports agent/probe/synthesis/total durations with source identity | Calls require explicit opt-in; ordinary checks use only fixtures | Offline check now; actual producer-PC run remains pending |

The implementation remains a preparation until the producer-PC measurement and
latency decision are recorded. Synthetic and other-OS checks establish behavior,
not acceptable latency while driving. Existing agent selection remains the opt-in
for sending its fact package and question to that provider; no agent or speech
provider is enabled automatically.

## Prepared measurement

The maintainer command reuses `LiveAI`, its process transport and the configured
speech adapters. It sends a documented synthetic fact set, the supplied question
and an empty conversation. It measures a fresh agent question and synthesis;
it never opens a microphone, plays audio, reads a recording or changes settings.
The selected agent must already be enabled and isolated in machine AI settings.
Use the same agent and voice as the producer's existing Engineer configuration.

Offline preparation check, with no real provider construction or calls:

```text
python tools/radio_live_benchmark.py --check
```

On the actual producer PC, once it is available, for configured local speech:

```text
python tools/radio_live_benchmark.py --machine-runtime "<producer runtime>" --agent "<engineer agent ID>" --speech kokoro --voice "<configured voice>" --allow-agent --agent-budget 30
```

For configured Google speech, use `--speech google --allow-cloud` and, when its
key is in the machine file rather than the environment, `--env-file "<machine .env>"`.
The key is read with the existing parser and never appears in argv or results.
Each explicit run admits at most two agent calls and two syntheses. Provider
configuration is unchanged. No personal-mode agent or provider fallback is used.

The measurement deadline defaults to thirty seconds and is capped at sixty, so
an answer taking longer than the live eight-second deadline can be measured.
This override exists only in the opt-in maintainer probe. Production defaults,
its caller budget and the service answer deadline remain eight seconds. Every
successful sample states whether its agent latency fits that live deadline.
A timed-out sample records elapsed time and does not invoke speech.

The JSON reports each probe/answer subprocess duration, agent time, synthesis
time, time until audio is ready, utterance length, validated answer, provider
status and source hashes/platform. It does not claim microphone-to-speaker or
in-race latency. Offline checks are labelled `synthetic_check` and are not a
performance measurement. Local Linux real measurements enter the repository's
existing finite CPU/memory/task containment before constructing providers.

## Acceptance still pending

Record the actual producer-PC output and inspect the probe/answer/synthesis split.
Then decide whether the existing live deadline and fixed fallback meet the driving
use case. The prepared code and its simulated evidence do not close #1065 by
claiming a hardware result that has not been measured.
